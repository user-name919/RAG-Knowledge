# 后台索引进程。使用 MySQL 任务表、行锁、租约和确定性 ES ID 实现可重试处理。
# 任务可能被重复执行，因此不能依赖“只执行一次”；需要通过版本和租约再次校验提交资格。

"""Durable MySQL queue. Each task indexes one immutable QA revision."""
from datetime import timedelta
import logging
import time
from sqlalchemy import and_, or_, select
from .chunks import qa_chunks
from .config import get_settings
from .db import SessionLocal
from .embedding import EmbeddingClient, EmbeddingError
from .models import Document, IndexTask, QAPair, FileIndexTask, uid, utcnow
from .file_indexer import process_file_task
from .search import SearchStore, SearchError

log = logging.getLogger(__name__)


# 在短事务中领取到期 pending 或租约已过期的 running 任务。
# SKIP LOCKED 跳过别的 Worker 正在锁定的行；每次领取生成新 token 并增加尝试次数。
def claim_task(factory=SessionLocal, task_model=IndexTask):
    settings, now = get_settings(), utcnow()
    # 领取操作必须在事务内完成，锁定任务与更新租约一起提交。
    with factory() as db, db.begin():
        task = db.scalar(select(task_model).where(or_(
            and_(task_model.status == 'pending', task_model.next_attempt_at <= now),
            and_(task_model.status == 'running', task_model.lease_until < now),
        )).order_by(task_model.created_at).with_for_update(skip_locked=True).limit(1))
        if not task:
            return None
        # 上限在领取前检查；手动重试会将 attempts 清零。
        if task.attempts >= settings.task_max_attempts:
            task.status = 'failed'
            task.last_error = 'Retry limit reached; inspect and manually retry'
            return None
        task.status, task.lease_token = 'running', uid()
        task.lease_until = now + timedelta(seconds=settings.task_lease_seconds)
        task.attempts += 1
        return task.id, task.lease_token


# 处理一次带租约的索引任务。外部模型和 ES 写入在初次读取事务之外执行，减少长时间占锁。
# 完成后重新锁定文档、QA、任务，校验租约所有者和版本，才能发布 indexed_version。
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
            # 读取后即使暂时有效，也可能在模型调用期间被更新，所以完成后仍需再次检查。
            stale = qa.version != version
            deleting = qa.deleted or document.deleted
            chunks = [] if stale or deleting else qa_chunks(qa, document, settings)

        # 没有任务时不主动调用 ES；有任务时先确保模型与索引结构兼容。
        store.ensure_index()
        if not stale and not deleting:
            # 模型与批量索引属于可能耗时、可能部分成功的外部操作，不在初次读事务里持有锁。
            vectors = embedder.embed([chunk['embedding_text'] for chunk in chunks])
            if len(vectors) != len(chunks):
                raise EmbeddingError('Embedding count mismatch')
            store.put_chunks(chunks, vectors)

        # Same lock order as API writes: document, QA, task. Recheck after external work.
        with factory() as db, db.begin():
            document = db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
            qa = db.scalar(select(QAPair).where(QAPair.id == qa_id).with_for_update())
            task = db.scalar(select(IndexTask).where(IndexTask.id == task_id).with_for_update())
            # 租约已经被接管时放弃发布；相同版本索引采用确定性 ID，后续持有者可重复写入。
            if task.lease_token != token or task.status != 'running':
                return
            # 任务已过期，只清理自己的版本。不能删整个 QA，否则可能破坏已完成的新版本。
            if qa.version != version:
                store.delete_qa(qa_id, exact_version=version)
                task.status = 'superseded'
            elif qa.deleted or document.deleted:
                store.delete_qa(qa_id)
                # 仅在对应 ES 操作成功后推进索引版本，与任务 done 状态在同一 MySQL 事务提交。
                qa.indexed_version = version
                task.status = 'done'
            else:
                store.delete_qa(qa_id, before_version=version)
                qa.indexed_version = version
                task.status = 'done'
            task.last_error, task.lease_until = None, None
    # 兜底失败处理不打印异常完整正文；未预期错误仅记录类型，避免 SQL/凭据进入日志。
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, (EmbeddingError, SearchError)) else type(exc).__name__ + ': indexing failed'
        with factory() as db, db.begin():
            task = db.scalar(select(IndexTask).where(IndexTask.id == task_id).with_for_update())
            if task and task.lease_token == token and task.status == 'running':
                task.last_error = safe_error[:500]
                task.status = 'failed' if task.attempts >= settings.task_max_attempts else 'pending'
                # 指数退避上限 300 秒，避免供应商或 ES 故障时不停重试。
                task.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** task.attempts * 5))
                task.lease_until = None
        log.warning('Task %s: %s', task_id, safe_error)


# Worker 常驻入口：不断领取任务，无任务时短暂休眠。
# 配置缺失会导致任务失败重试；修复配置并重启后可用 retry_failed 恢复失败任务。
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
            # 每轮两类任务各处理一个，避免 QA 队列持续有任务时饿死文件队列。
            file_claim = claim_task(task_model=FileIndexTask)
            if file_claim:
                process_file_task(*file_claim)
            if not claim and not file_claim:
                time.sleep(get_settings().worker_poll_seconds)
        except Exception as exc:
            # 队列连接本身失败时，任务可能尚未领取；循环等待后继续，而不是直接退出进程。
            log.error('Queue error: %s', type(exc).__name__)
            time.sleep(5)


if __name__ == '__main__':
    main()
