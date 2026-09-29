# FastAPI 接口层：知识管理、凭据管理、任务状态查询和混合召回。
# 写入链路：保存 QA 与任务到同一事务 → Worker 建索引。
# 查询链路：鉴权 → 模型向量化 → PostgreSQL 双路检索 → PostgreSQL 校验有效版本 → 返回原文。

from contextlib import asynccontextmanager
from pathlib import Path
import hashlib
import json
import secrets
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .auth import authenticate, authorize, require_admin
from .config import get_settings
from .db import engine, get_db
from .embedding import EmbeddingClient, EmbeddingError
from .models import APIKey, Base, Document, DocumentFile, FileIndexTask, IndexTask, KnowledgeBase, QAPair, utcnow
from .documents import router as file_router, file_payload
from .tokenization import token_count
from .upload_limit import UploadLimitMiddleware
from .schemas import DocumentCreate, KBCreate, KeyCreate, QABatch, QAContent, RecallRequest
from .search import SearchError, SearchStore, fuse


# 服务启动时校验管理员凭据、创建缺失的表并确认 检索索引配置。
# create_all 不会迁移已有表结构；索引模型不匹配时应阻止启动，避免混用向量。
@asynccontextmanager
async def lifespan(app):
    if len(get_settings().admin_api_key) < 24:
        raise RuntimeError('Set ADMIN_API_KEY to a random value of at least 24 characters')
    # 只创建不存在的表，不能替代数据库迁移工具。
    Base.metadata.create_all(engine)
    SearchStore().ensure_index()
    yield


app = FastAPI(title='团队 RAG 知识库', version='0.3.0', lifespan=lifespan,
              description='QA 入库、版本维护、索引任务和向量 0.8 + 关键词 0.2 混合召回。无答案生成。')
app.add_middleware(UploadLimitMiddleware, max_bytes=get_settings().max_upload_bytes + 128 * 1024)
app.include_router(file_router)

# Vue 构建产物随镜像提供，同源调用 API，不需要在浏览器配置跨域。
ui_dir = Path(__file__).parent / 'ui'
if (ui_dir / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=ui_dir / 'assets'), name='ui-assets')


@app.get('/', include_in_schema=False)
def demo_page():
    if not (ui_dir / 'index.html').exists():
        raise HTTPException(503, 'Frontend not built; run npm run build in frontend')
    return FileResponse(ui_dir / 'index.html')


@app.get('/v1/me', tags=['访问凭据'])
def me(principal=Depends(authenticate)):
    return {'admin': principal.admin, 'can_write': principal.can_write, 'knowledge_base_ids': principal.knowledge_base_ids}



# 将数据库唯一约束等完整性冲突转换为 409，不把 SQL 或数据库内部错误返回给客户端。
@app.exception_handler(IntegrityError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={'detail': 'Concurrent or duplicate write; retry with the same external_id'})


# 同时按知识库 ID 和文档 ID 读取节点，避免通过别的库的文档 ID 绕过范围。
# 写路径可加行锁；删除任务状态查询可显式包含已删除节点。
def document_for(db, kb_id, doc_id, lock=False, include_deleted=False):
    stmt = select(Document).where(Document.id == doc_id, Document.knowledge_base_id == kb_id)
    if lock:
        stmt = stmt.with_for_update()
    doc = db.scalar(stmt)
    if not doc or (doc.deleted and not include_deleted):
        raise HTTPException(404, 'Document not found')
    return doc


# 只选取公开 QA 字段作为响应，包含原文和两个版本号，便于调用方核对索引进度。
def qa_payload(qa):
    return {k: getattr(qa, k) for k in ('id', 'document_id', 'external_id', 'question', 'answer', 'stand_query', 'tags', 'version', 'indexed_version', 'deleted', 'updated_at')}


# 为 QA 当前版本创建任务并 flush 取得 ID，但不提交。
# 调用者负责与 QA 内容一同提交，保证不会出现“内容写了但任务丢了”。
def add_task(db, qa):
    task = IndexTask(qa_id=qa.id, version=qa.version)
    db.add(task)
    db.flush()
    return task


# 检查数据库查询和 检索索引可访问性。embedding_configured 仅表示 Key 非空，不代表已成功调用模型。
@app.get('/health', tags=['运行状态'])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        SearchStore().count()
    except Exception:
        raise HTTPException(503, 'Database or search service unavailable') from None
    return {'status': 'ok', 'embedding_configured': bool(get_settings().embedding_api_key)}


# 验证授权知识库存在后生成高熵 Key，仅在创建响应里返回一次明文，数据库只持久化摘要。
@app.post('/v1/api_keys', tags=['访问凭据'], status_code=201)
def create_key(body: KeyCreate, principal=Depends(require_admin), db: Session = Depends(get_db)):
    for kb in body.knowledge_base_ids:
        if not db.get(KnowledgeBase, kb):
            raise HTTPException(404, 'Knowledge base not found')
    # 应用 Key 使用随机值而非用户可猜测字符串；后续仅按 SHA-256 摘要查找。
    secret = 'rag_' + secrets.token_urlsafe(32)
    key = APIKey(name=body.name, key_hash=hashlib.sha256(secret.encode()).hexdigest(), knowledge_base_ids=list(set(body.knowledge_base_ids)), can_write=body.can_write)
    db.add(key)
    db.commit()
    return {'id': key.id, 'api_key': secret, 'note': 'Returned once; store securely'}


# 将普通 Key 标为失效，后续请求的认证查询不再接受它。
@app.delete('/v1/api_keys/{key_id}', tags=['访问凭据'])
def revoke_key(key_id: str, principal=Depends(require_admin), db: Session = Depends(get_db)):
    key = db.get(APIKey, key_id)
    if not key:
        raise HTTPException(404, 'Key not found')
    key.active = False
    db.commit()
    return {'revoked': True}


# 管理员创建新的知识隔离范围，普通 Key 不能自行扩展到新知识库。
@app.post('/v1/knowledge_bases', tags=['知识库'], status_code=201)
def create_kb(body: KBCreate, principal=Depends(require_admin), db: Session = Depends(get_db)):
    kb = KnowledgeBase(name=body.name)
    db.add(kb)
    db.commit()
    return {'id': kb.id, 'name': kb.name}


# 返回调用方可见的知识库，并使用稳定排序和 offset/limit 分页。
@app.get('/v1/knowledge_bases', tags=['知识库'])
def list_kbs(principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at, KnowledgeBase.id)
    if not principal.admin:
        stmt = stmt.where(KnowledgeBase.id.in_(principal.knowledge_base_ids))
    return {'data': [{'id': row.id, 'name': row.name} for row in db.scalars(stmt.offset(offset).limit(limit))]}


# 在已有且有写权限的知识库内创建 QA 容器，不进行文件解析或向量化。
@app.post('/v1/knowledge_bases/{kb_id}/documents', tags=['文档节点'], status_code=201)
def create_document(kb_id: str, body: DocumentCreate, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    if not db.get(KnowledgeBase, kb_id):
        raise HTTPException(404, 'Knowledge base not found')
    doc = Document(knowledge_base_id=kb_id, **body.model_dump())
    db.add(doc)
    db.commit()
    return {'id': doc.id, 'title': doc.title, 'knowledge_base_id': kb_id}


# 分页列出指定知识库中未软删除的文档节点。
@app.get('/v1/knowledge_bases/{kb_id}/documents', tags=['文档节点'])
def list_documents(kb_id: str, principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    authorize(principal, [kb_id])
    stmt = select(Document, DocumentFile, FileIndexTask).outerjoin(DocumentFile, DocumentFile.document_id == Document.id).outerjoin(FileIndexTask, (FileIndexTask.document_id == Document.id) & (FileIndexTask.version == DocumentFile.version)).where(Document.knowledge_base_id == kb_id, Document.deleted.is_(False)).order_by(Document.created_at, Document.id).offset(offset).limit(limit)
    return {'data': [{'id': d.id, 'title': d.title, 'source_uri': d.source_uri, 'type': 'document' if f else 'qa', 'file': file_payload(f, t) if f else None} for d, f, t in db.execute(stmt)]}


# 批量持久化 QA 与索引任务，返回 202 表示接收成功，尚未保证可检索。
# 同 external_id 同内容复用记录，不同内容或已删除记录返回 409；整批冲突会回滚。
@app.post('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/qa_pairs/batch_create', tags=['QA'], status_code=202)
def create_qa_batch(kb_id: str, doc_id: str, body: QABatch, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    # 写路径先锁文档再处理 QA，与 Worker 保持相同锁顺序，降低交叉更新死锁风险。
    document_for(db, kb_id, doc_id, lock=True)
    results = []
    for item in body.qa_pairs:
        # 标准问法统一补齐后再比较和计算摘要，确保省略与显式填写原问法的请求等价。
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
            # 立即分配 QA ID 供任务引用，但还未 commit；后续条目失败时仍可整批回滚。
            db.flush()
            task = add_task(db, qa)
        results.append({'id': qa.id, 'external_id': external_id, 'version': qa.version, 'task_id': task.id, 'index_status': task.status, 'reused': reused})
    db.commit()
    return {'object': 'list', 'data': results, 'note': 'Saved; wait for index_status=ready before recall'}


# 查看文档下有效 QA 的权威原文，不从 检索片段 读取可能过期的副本。
@app.get('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/qa_pairs', tags=['QA'])
def list_qa(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    authorize(principal, [kb_id])
    document_for(db, kb_id, doc_id)
    rows = db.scalars(select(QAPair).where(QAPair.document_id == doc_id, QAPair.deleted.is_(False)).order_by(QAPair.id).offset(offset).limit(limit))
    return {'data': [qa_payload(q) for q in rows]}


# 整体替换 QA 正文；内容真正变化时才递增版本并入队。
# 版本变化立即让旧 检索片段 片段无法通过有效性校验，新版完成前该 QA 暂时不可召回。
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


# 幂等软删除单条 QA，同时创建新版本任务清理 检索索引；不等待清理完成才使记录失效。
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


# 软删除节点及全部有效 QA，在一个事务内为每个 QA 创建清理任务。
# 保留历史行，使后台可完成清理，并支持查看删除进度。
@app.delete('/v1/knowledge_bases/{kb_id}/documents/{doc_id}', tags=['文档节点'])
def delete_document(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    doc = document_for(db, kb_id, doc_id, lock=True, include_deleted=True)
    record = db.get(DocumentFile, doc.id)
    if record and not doc.deleted:
        record.version += 1
        db.add(FileIndexTask(document_id=doc.id, version=record.version))
    doc.deleted = True
    for qa in db.scalars(select(QAPair).where(QAPair.document_id == doc_id, QAPair.deleted.is_(False)).with_for_update()):
        qa.deleted = True
        qa.version += 1
        add_task(db, qa)
    db.commit()
    return {'id': doc.id, 'deleted': True}


# 将每条 QA 与其当前版本任务连接，忽略历史版本任务。
# 任务 done 且索引版本一致才映射为 ready；删除完成映射为 deleted。
@app.get('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/index_status', tags=['索引'])
def index_status(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    authorize(principal, [kb_id])
    doc = document_for(db, kb_id, doc_id, include_deleted=True)
    record = db.get(DocumentFile, doc_id)
    task = db.scalar(select(FileIndexTask).where(FileIndexTask.document_id == doc_id, FileIndexTask.version == record.version)) if record else None
    file_info = file_payload(record, task) if record else None
    if file_info and doc.deleted and file_info['index_status'] == 'ready':
        file_info['index_status'] = 'deleted'
    rows = db.execute(select(QAPair, IndexTask).join(IndexTask, (IndexTask.qa_id == QAPair.id) & (IndexTask.version == QAPair.version)).where(QAPair.document_id == doc_id).order_by(QAPair.id).offset(offset).limit(limit))
    return {'file': file_info, 'data': [{'qa_id': q.id, 'version': q.version, 'indexed_version': q.indexed_version,
                      'index_status': ('deleted' if q.deleted else 'ready') if t.status == 'done' and q.indexed_version == q.version else t.status,
                      'task_id': t.id, 'attempts': t.attempts, 'last_error': t.last_error} for q, t in rows]}


# 只重置当前版本的失败任务，不复活已过期版本；允许为删除节点继续清理失败的索引。
@app.post('/v1/knowledge_bases/{kb_id}/documents/{doc_id}/retry_failed', tags=['索引'])
def retry_failed(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id], write=True)
    document_for(db, kb_id, doc_id, lock=True, include_deleted=True)
    tasks = db.scalars(select(IndexTask).join(QAPair, (QAPair.id == IndexTask.qa_id) & (QAPair.version == IndexTask.version)).where(QAPair.document_id == doc_id, IndexTask.status == 'failed').with_for_update()).all()
    record = db.get(DocumentFile, doc_id)
    if record:
        tasks += db.scalars(select(FileIndexTask).where(FileIndexTask.document_id == doc_id, FileIndexTask.version == record.version, FileIndexTask.status == 'failed').with_for_update()).all()
    for task in tasks:
        task.status, task.attempts, task.last_error = 'pending', 0, None
        task.next_attempt_at = utcnow()
    db.commit()
    return {'retried': len(tasks)}


# 批量读取候选对应的 PostgreSQL 记录，过滤不存在、已删除、越权或版本不一致的 检索片段 副本。
# 这一步保留融合排序，不重新打分；过滤后结果可能少于 top_k。
def valid_candidates(db, candidates, kb_ids, document_ids, tags):
    """业务版本是权威来源；过滤已删除、过期或越权的检索片段。"""
    if not candidates:
        return []
    # 一次读取全部候选对应的 QA，避免对每个结果单独查询数据库。
    qa_ids = list({r['qa_id'] for r in candidates if r.get('qa_id')})
    rows = db.execute(select(QAPair, Document).join(Document, QAPair.document_id == Document.id).where(QAPair.id.in_(qa_ids)))
    current = {q.id: (q, d) for q, d in rows}
    file_ids = list({r['document_id'] for r in candidates if r.get('type') == 'document'})
    files = {f.document_id: (f, d) for f, d in db.execute(select(DocumentFile, Document).join(Document, Document.id == DocumentFile.document_id).where(DocumentFile.document_id.in_(file_ids)))}
    valid = []
    for row in candidates:
        if row.get('type') == 'document':
            pair = files.get(row['document_id'])
            if not pair:
                continue
            f, d = pair
            if d.deleted or d.knowledge_base_id not in kb_ids or row.get('knowledge_base_id') != d.knowledge_base_id:
                continue
            if document_ids and d.id not in document_ids:
                continue
            if not set(tags).issubset(f.tags) or row['version'] != f.version or f.indexed_version != f.version:
                continue
            valid.append(row)
            continue
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
        # 同时要求候选版本等于当前内容版本，且当前版本已发布；只比较其中一个会放过半成品。
        if row['version'] != qa.version or qa.indexed_version != qa.version:
            continue
        valid.append(row)
    return valid


# 执行权限与范围预检，再进行问题向量化、双路召回、融合、数据库复核和阈值裁剪。
# 返回原文与来源，不调用聊天模型；外部依赖故障返回 503，而不是空结果冒充无答案。
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
    if token_count(get_settings().embedding_query_prefix + body.query) > 512:
        raise HTTPException(422, 'Query exceeds the 512-token model limit')
    # End preflight snapshot so post-search validation sees commits made during retrieval.
    db.commit()
    try:
        vector = EmbeddingClient().embed([body.query], query=True)[0]
        options = body.retrieval_options
        # 多取候选给融合和失效过滤留出空间；设上限避免 top_k 放大成无界检索。
        candidates = min(500, max(40, options.top_k * 5))
        semantic, keyword = SearchStore().search(body.query, vector, body.knowledge_base_ids, body.document_ids, options.tags, candidates)
        fused = fuse(semantic, keyword, body.weights.vector_setting.vector_weight, body.weights.keyword_setting.vector_weight)
    except (EmbeddingError, SearchError) as exc:
        raise HTTPException(503, str(exc)) from None
    rows = valid_candidates(db, fused, body.knowledge_base_ids, body.document_ids, options.tags)
    # 先确认原文有效，再应用融合后的阈值，最后截断 top_k；默认 0 阈值不保证拒绝无关资料。
    data = [dict(r, object='knowledge_base.document.chunk', type=r.get('type', 'qa')) for r in rows if r['score'] >= options.score_threshold][:options.top_k]
    return {'object': 'list', 'total': len(data), 'data': data,
            'score_definition': 'vector_weight*(1+cosine)/2 + keyword_weight*ts_rank_cd(normalization=32); field weights=3:2:1; missing branch=0; not confidence',
            'candidate_limit_per_branch': candidates}
