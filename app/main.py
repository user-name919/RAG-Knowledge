from contextlib import asynccontextmanager
import hashlib
import json
import secrets
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .auth import authenticate, authorize, require_admin
from .config import get_settings
from .db import engine, get_db
from .embedding import EmbeddingClient, EmbeddingError
from .models import APIKey, Base, Document, IndexTask, KnowledgeBase, QAPair, utcnow
from .schemas import DocumentCreate, KBCreate, KeyCreate, QABatch, QAContent, RecallRequest
from .search import SearchError, SearchStore, fuse


@asynccontextmanager
async def lifespan(app):
    if len(get_settings().admin_api_key) < 24:
        raise RuntimeError('Set ADMIN_API_KEY to a random value of at least 24 characters')
    Base.metadata.create_all(engine)
    SearchStore().ensure_index()
    yield


app = FastAPI(title='团队 RAG 知识库', version='0.1.0', lifespan=lifespan,
              description='QA 入库、版本维护、索引任务和向量 0.8 + 关键词 0.2 混合召回。无答案生成。')


@app.exception_handler(IntegrityError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={'detail': 'Concurrent or duplicate write; retry with the same external_id'})


def document_for(db, kb_id, doc_id, lock=False, include_deleted=False):
    stmt = select(Document).where(Document.id == doc_id, Document.knowledge_base_id == kb_id)
    if lock:
        stmt = stmt.with_for_update()
    doc = db.scalar(stmt)
    if not doc or (doc.deleted and not include_deleted):
        raise HTTPException(404, 'Document not found')
    return doc


def qa_payload(qa):
    return {k: getattr(qa, k) for k in ('id', 'document_id', 'external_id', 'question', 'answer', 'stand_query', 'tags', 'version', 'indexed_version', 'deleted', 'updated_at')}


def add_task(db, qa):
    task = IndexTask(qa_id=qa.id, version=qa.version)
    db.add(task)
    db.flush()
    return task


@app.get('/health', tags=['运行状态'])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        SearchStore().request('GET', '/' + get_settings().es_index + '/_count')
    except Exception:
        raise HTTPException(503, 'Database or search service unavailable') from None
    return {'status': 'ok', 'embedding_configured': bool(get_settings().embedding_api_key)}


@app.post('/v1/api_keys', tags=['访问凭据'], status_code=201)
def create_key(body: KeyCreate, principal=Depends(require_admin), db: Session = Depends(get_db)):
    for kb in body.knowledge_base_ids:
        if not db.get(KnowledgeBase, kb):
            raise HTTPException(404, 'Knowledge base not found')
    secret = 'rag_' + secrets.token_urlsafe(32)
    key = APIKey(name=body.name, key_hash=hashlib.sha256(secret.encode()).hexdigest(), knowledge_base_ids=list(set(body.knowledge_base_ids)), can_write=body.can_write)
    db.add(key)
    db.commit()
    return {'id': key.id, 'api_key': secret, 'note': 'Returned once; store securely'}


@app.delete('/v1/api_keys/{key_id}', tags=['访问凭据'])
def revoke_key(key_id: str, principal=Depends(require_admin), db: Session = Depends(get_db)):
    key = db.get(APIKey, key_id)
    if not key:
        raise HTTPException(404, 'Key not found')
    key.active = False
    db.commit()
    return {'revoked': True}


@app.post('/v1/knowledge_bases', tags=['知识库'], status_code=201)
def create_kb(body: KBCreate, principal=Depends(require_admin), db: Session = Depends(get_db)):
    kb = KnowledgeBase(name=body.name)
    db.add(kb)
    db.commit()
    return {'id': kb.id, 'name': kb.name}


@app.get('/v1/knowledge_bases', tags=['知识库'])
def list_kbs(principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at, KnowledgeBase.id)
    if not principal.admin:
        stmt = stmt.where(KnowledgeBase.id.in_(principal.knowledge_base_ids))
    return {'data': [{'id': row.id, 'name': row.name} for row in db.scalars(stmt.offset(offset).limit(limit))]}


@app.post('/v1/knowledge_bases/{kb_id}/documents', tags=['文档节点'], status_code=201)
def create_document(kb_id: str, body: DocumentCreate, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    if not db.get(KnowledgeBase, kb_id):
        raise HTTPException(404, 'Knowledge base not found')
    doc = Document(knowledge_base_id=kb_id, **body.model_dump())
    db.add(doc)
    db.commit()
    return {'id': doc.id, 'title': doc.title, 'knowledge_base_id': kb_id}


@app.get('/v1/knowledge_bases/{kb_id}/documents', tags=['文档节点'])
def list_documents(kb_id: str, principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    authorize(principal, [kb_id])
    stmt = select(Document).where(Document.knowledge_base_id == kb_id, Document.deleted.is_(False)).order_by(Document.created_at, Document.id).offset(offset).limit(limit)
    return {'data': [{'id': d.id, 'title': d.title, 'source_uri': d.source_uri} for d in db.scalars(stmt)]}


@app.post('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/qa_pairs/batch_create', tags=['QA'], status_code=202)
def create_qa_batch(kb_id: str, doc_id: str, body: QABatch, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    document_for(db, kb_id, doc_id, lock=True)
    results = []
    for item in body.qa_pairs:
        values = item.model_dump(exclude={'external_id'})
        values['stand_query'] = item.stand_query or item.question
        # Identical requests get the same ID; callers should supply stable external IDs for mutable source records.
        external_id = item.external_id or hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        qa = db.scalar(select(QAPair).where(QAPair.document_id == doc_id, QAPair.external_id == external_id))
        reused = qa is not None
        if qa:
            if qa.deleted or any(getattr(qa, k) != v for k, v in values.items()):
                raise HTTPException(409, 'external_id already exists with different/deleted content; use update or a new external_id')
            task = db.scalar(select(IndexTask).where(IndexTask.qa_id == qa.id, IndexTask.version == qa.version))
        else:
            qa = QAPair(document_id=doc_id, external_id=external_id, **values)
            db.add(qa)
            db.flush()
            task = add_task(db, qa)
        results.append({'id': qa.id, 'external_id': external_id, 'version': qa.version, 'task_id': task.id, 'index_status': task.status, 'reused': reused})
    db.commit()
    return {'object': 'list', 'data': results, 'note': 'Saved; wait for index_status=ready before recall'}


@app.get('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/qa_pairs', tags=['QA'])
def list_qa(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    authorize(principal, [kb_id])
    document_for(db, kb_id, doc_id)
    rows = db.scalars(select(QAPair).where(QAPair.document_id == doc_id, QAPair.deleted.is_(False)).order_by(QAPair.id).offset(offset).limit(limit))
    return {'data': [qa_payload(q) for q in rows]}


@app.put('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/qa_pairs/{qa_id}', tags=['QA'])
def update_qa(kb_id: str, doc_id: str, qa_id: str, body: QAContent, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    document_for(db, kb_id, doc_id, lock=True)
    qa = db.scalar(select(QAPair).where(QAPair.id == qa_id, QAPair.document_id == doc_id, QAPair.deleted.is_(False)).with_for_update())
    if not qa:
        raise HTTPException(404, 'QA not found')
    values = body.model_dump()
    values['stand_query'] = body.stand_query or body.question
    if any(getattr(qa, k) != v for k, v in values.items()):
        for key, value in values.items():
            setattr(qa, key, value)
        qa.version += 1
        add_task(db, qa)
    db.commit()
    return qa_payload(qa)


@app.delete('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/qa_pairs/{qa_id}', tags=['QA'])
def delete_qa(kb_id: str, doc_id: str, qa_id: str, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    document_for(db, kb_id, doc_id, lock=True)
    qa = db.scalar(select(QAPair).where(QAPair.id == qa_id, QAPair.document_id == doc_id).with_for_update())
    if not qa:
        raise HTTPException(404, 'QA not found')
    if not qa.deleted:
        qa.deleted = True
        qa.version += 1
        add_task(db, qa)
    db.commit()
    return {'id': qa.id, 'deleted': True}


@app.delete('/v1/knowledge_bases/{kb_id}/documents/{doc_id}', tags=['文档节点'])
def delete_document(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    doc = document_for(db, kb_id, doc_id, lock=True, include_deleted=True)
    doc.deleted = True
    for qa in db.scalars(select(QAPair).where(QAPair.document_id == doc_id, QAPair.deleted.is_(False)).with_for_update()):
        qa.deleted = True
        qa.version += 1
        add_task(db, qa)
    db.commit()
    return {'id': doc.id, 'deleted': True}


@app.get('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/index_status', tags=['索引'])
def index_status(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    authorize(principal, [kb_id])
    document_for(db, kb_id, doc_id, include_deleted=True)
    rows = db.execute(select(QAPair, IndexTask).join(IndexTask, (IndexTask.qa_id == QAPair.id) & (IndexTask.version == QAPair.version)).where(QAPair.document_id == doc_id).order_by(QAPair.id).offset(offset).limit(limit))
    return {'data': [{'qa_id': q.id, 'version': q.version, 'indexed_version': q.indexed_version,
                      'index_status': ('deleted' if q.deleted else 'ready') if t.status == 'done' and q.indexed_version == q.version else t.status,
                      'task_id': t.id, 'attempts': t.attempts, 'last_error': t.last_error} for q, t in rows]}


@app.post('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/retry_failed', tags=['索引'])
def retry_failed(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    document_for(db, kb_id, doc_id, lock=True, include_deleted=True)
    tasks = db.scalars(select(IndexTask).join(QAPair, (QAPair.id == IndexTask.qa_id) & (QAPair.version == IndexTask.version)).where(QAPair.document_id == doc_id, IndexTask.status == 'failed').with_for_update()).all()
    for task in tasks:
        task.status, task.attempts, task.last_error = 'pending', 0, None
        task.next_attempt_at = utcnow()
    db.commit()
    return {'retried': len(tasks)}


def valid_candidates(db, candidates, kb_ids, document_ids, tags):
    """DB is authoritative. Never return deleted, stale or out-of-scope ES records."""
    if not candidates:
        return []
    qa_ids = list({r['qa_id'] for r in candidates})
    rows = db.execute(select(QAPair, Document).join(Document, QAPair.document_id == Document.id).where(QAPair.id.in_(qa_ids)))
    current = {q.id: (q, d) for q, d in rows}
    valid = []
    for row in candidates:
        pair = current.get(row['qa_id'])
        if not pair:
            continue
        qa, doc = pair
        if qa.deleted or doc.deleted or doc.knowledge_base_id not in kb_ids:
            continue
        if row.get('document_id') != doc.id or row.get('knowledge_base_id') != doc.knowledge_base_id:
            continue
        if document_ids and doc.id not in document_ids:
            continue
        if not set(tags).issubset(qa.tags):
            continue
        if row['version'] != qa.version or qa.indexed_version != qa.version:
            continue
        valid.append(row)
    return valid


@app.post('/v1/knowledge_bases/recall', tags=['检索'])
def recall(body: RecallRequest, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, body.knowledge_base_ids)
    for kb in body.knowledge_base_ids:
        if not db.get(KnowledgeBase, kb):
            raise HTTPException(404, 'Knowledge base not found')
    for doc_id in body.document_ids:
        doc = db.get(Document, doc_id)
        if not doc or doc.deleted or doc.knowledge_base_id not in body.knowledge_base_ids:
            raise HTTPException(404, 'Document not found in requested knowledge bases')
    if len((get_settings().embedding_query_prefix + body.query).encode()) > 480:
        raise HTTPException(422, 'Query too long for this model; shorten to roughly 130 Chinese characters')
    # End preflight snapshot so post-search validation sees commits made during retrieval.
    db.commit()
    try:
        vector = EmbeddingClient().embed([body.query], query=True)[0]
        options = body.retrieval_options
        candidates = min(500, max(40, options.top_k * 5))
        semantic, keyword = SearchStore().search(body.query, vector, body.knowledge_base_ids, body.document_ids, options.tags, candidates)
        fused = fuse(semantic, keyword, body.weights.vector_setting.vector_weight, body.weights.keyword_setting.vector_weight)
    except (EmbeddingError, SearchError) as exc:
        raise HTTPException(503, str(exc)) from None
    rows = valid_candidates(db, fused, body.knowledge_base_ids, body.document_ids, options.tags)
    data = [dict(r, object='knowledge_base.document.chunk', type='qa') for r in rows if r['score'] >= options.score_threshold][:options.top_k]
    return {'object': 'list', 'total': len(data), 'data': data,
            'score_definition': '0.8*cosine_ES_score + 0.2*BM25/(BM25+8), with configured weights; missing branch=0; not confidence',
            'candidate_limit_per_branch': candidates}
