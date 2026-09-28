"""Durable MySQL queue. Each task indexes one immutable QA revision."""
from datetime import timedelta
import logging
import time
from sqlalchemy import and_, or_, select
from .chunks import qa_chunks
from .config import get_settings
from .db import SessionLocal
from .embedding import EmbeddingClient, EmbeddingError
from .models import Document, IndexTask, QAPair, uid, utcnow
from .search import SearchStore, SearchError

log = logging.getLogger(__name__)


def claim_task(factory=SessionLocal):
    settings, now = get_settings(), utcnow()
    with factory() as db, db.begin():
        task = db.scalar(select(IndexTask).where(or_(
            and_(IndexTask.status == 'pending', IndexTask.next_attempt_at <= now),
            and_(IndexTask.status == 'running', IndexTask.lease_until < now),
        )).order_by(IndexTask.created_at).with_for_update(skip_locked=True).limit(1))
        if not task:
            return None
        if task.attempts >= settings.task_max_attempts:
            task.status = 'failed'
            task.last_error = 'Retry limit reached; inspect and manually retry'
            return None
        task.status, task.lease_token = 'running', uid()
        task.lease_until = now + timedelta(seconds=settings.task_lease_seconds)
        task.attempts += 1
        return task.id, task.lease_token


def process_task(task_id, token, factory=SessionLocal, embedder=None, store=None):
    settings = get_settings()
    embedder, store = embedder or EmbeddingClient(), store or SearchStore()
    try:
        with factory() as db:
            task = db.get(IndexTask, task_id)
            if task.status != 'running' or task.lease_token != token:
                return
            qa = db.get(QAPair, task.qa_id)
            document = db.get(Document, qa.document_id)
            version, qa_id, doc_id = task.version, qa.id, document.id
            stale = qa.version != version
            deleting = qa.deleted or document.deleted
            chunks = [] if stale or deleting else qa_chunks(qa, document, settings)

        store.ensure_index()
        if not stale and not deleting:
            vectors = embedder.embed([chunk['embedding_text'] for chunk in chunks])
            if len(vectors) != len(chunks):
                raise EmbeddingError('Embedding count mismatch')
            store.put_chunks(chunks, vectors)

        # Same lock order as API writes: document, QA, task. Recheck after external work.
        with factory() as db, db.begin():
            document = db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
            qa = db.scalar(select(QAPair).where(QAPair.id == qa_id).with_for_update())
            task = db.scalar(select(IndexTask).where(IndexTask.id == task_id).with_for_update())
            if task.lease_token != token or task.status != 'running':
                return
            if qa.version != version:
                store.delete_qa(qa_id, exact_version=version)
                task.status = 'superseded'
            elif qa.deleted or document.deleted:
                store.delete_qa(qa_id)
                qa.indexed_version = version
                task.status = 'done'
            else:
                store.delete_qa(qa_id, before_version=version)
                qa.indexed_version = version
                task.status = 'done'
            task.last_error, task.lease_until = None, None
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, (EmbeddingError, SearchError)) else type(exc).__name__ + ': indexing failed'
        with factory() as db, db.begin():
            task = db.scalar(select(IndexTask).where(IndexTask.id == task_id).with_for_update())
            if task and task.lease_token == token and task.status == 'running':
                task.last_error = safe_error[:500]
                task.status = 'failed' if task.attempts >= settings.task_max_attempts else 'pending'
                task.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** task.attempts * 5))
                task.lease_until = None
        log.warning('Task %s: %s', task_id, safe_error)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    if not get_settings().embedding_api_key:
        log.warning('EMBEDDING_API_KEY missing: index tasks will retry/fail until configured and worker restarted')
    while True:
        try:
            claim = claim_task()
            if claim:
                process_task(*claim)
            else:
                time.sleep(get_settings().worker_poll_seconds)
        except Exception as exc:
            log.error('Queue error: %s', type(exc).__name__)
            time.sleep(5)


if __name__ == '__main__':
    main()
