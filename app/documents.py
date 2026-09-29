# 文件上传与原文管理。原文件保存到共享卷，MySQL 保存其当前版本；向量化交给后台任务。
import hashlib
import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import authenticate, authorize
from .config import get_settings
from .db import get_db
from .models import Document, DocumentFile, FileIndexTask, KnowledgeBase, uid

router = APIRouter(prefix='/v1/knowledge_bases', tags=['文件文档'])
ALLOWED = {'.md', '.markdown', '.txt', '.pdf'}


# 只接受服务端生成的文件路径，并确保其仍位于上传目录下。
def stored_path(relative):
    root = Path(get_settings().upload_dir).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError('Invalid storage path')
    return path


def file_payload(record, task=None):
    status = 'ready' if record.indexed_version == record.version else (task.status if task else 'pending')
    return {'filename': record.filename, 'size_bytes': record.size_bytes, 'version': record.version,
            'indexed_version': record.indexed_version, 'chunk_count': record.chunk_count,
            'tags': record.tags, 'warnings': record.warnings, 'index_status': status,
            'last_error': task.last_error if task else None, 'updated_at': record.updated_at}


# 上传新增文档及替换内容复用同一条写入逻辑；所有内容校验通过后才写数据库。
def save_upload(kb_id, upload, tags_json, title, db, principal, doc_id=None):
    authorize(principal, [kb_id], write=True)
    if not db.get(KnowledgeBase, kb_id):
        raise HTTPException(404, 'Knowledge base not found')
    filename = Path((upload.filename or '').replace('\\', '/')).name[:255]
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(415, '仅支持 Markdown、TXT 和文本型 PDF')
    try:
        tags = json.loads(tags_json)
        if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(x, str) or not x.strip() or len(x) > 64 for x in tags):
            raise ValueError()
        tags = list(dict.fromkeys(x.strip() for x in tags))
    except (ValueError, TypeError):
        raise HTTPException(422, 'tags 必须是最多 20 个非空字符串组成的 JSON 数组') from None
    data = upload.file.read(get_settings().max_upload_bytes + 1)
    if not data:
        raise HTTPException(422, '文件为空')
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(413, '单个文件最大 10 MB')
    if suffix == '.pdf' and not data.startswith(b'%PDF-'):
        raise HTTPException(422, '文件不是有效的 PDF')
    if suffix != '.pdf':
        try:
            text = data.decode('utf-8-sig')
            if '\x00' in text or not text.strip():
                raise ValueError()
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(422, '文本文件需要使用 UTF-8 编码，且包含有效正文') from None
    if title is not None and (not title.strip() or len(title.strip()) > 200):
        raise HTTPException(422, '标题长度应为 1～200 字符')
    digest = hashlib.sha256(data).hexdigest()
    if doc_id:
        doc = db.scalar(select(Document).where(Document.id == doc_id, Document.knowledge_base_id == kb_id).with_for_update())
        record = db.get(DocumentFile, doc_id)
        if not doc or doc.deleted or not record:
            raise HTTPException(404, '文件文档不存在')
        # 未改变内容、文件名或标签时不重复生成向量。
        if record.sha256 == digest and record.filename == filename and record.tags == tags and (title is None or title.strip() == doc.title):
            task = db.scalar(select(FileIndexTask).where(FileIndexTask.document_id == doc_id, FileIndexTask.version == record.version))
            return {'id': doc.id, 'title': doc.title, 'reused': True, **file_payload(record, task)}
        old_path = record.storage_path
        version = record.version + 1
    else:
        doc = Document(id=uid(), knowledge_base_id=kb_id, title=(title or filename).strip()[:200])
        record = DocumentFile(document_id=doc.id)
        old_path, version = None, 1
    relative = f'{doc.id}/{version}-{uid()}{suffix}'
    target = stored_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 不使用上传文件名作为磁盘路径，防止路径穿越和同名文件覆盖。
        with target.open('xb') as handle:
            handle.write(data)
        if title is not None:
            doc.title = title.strip()
        doc.source_uri = f'/v1/knowledge_bases/{kb_id}/documents/{doc.id}/file'
        db.add(doc)
        db.flush()
        record.filename, record.storage_path, record.sha256 = filename, relative, digest
        record.size_bytes, record.tags, record.version = len(data), tags, version
        record.chunk_count, record.warnings = 0, []
        db.add(record)
        task = FileIndexTask(document_id=doc.id, version=version)
        db.add(task)
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    # 新记录提交后再清理旧原文件；旧任务即使正在读取，也不能发布旧版本。
    if old_path:
        stored_path(old_path).unlink(missing_ok=True)
    return {'id': doc.id, 'title': doc.title, 'reused': False, **file_payload(record, task)}


@router.post('/{kb_id}/documents/upload', status_code=202)
def upload_document(kb_id: str, file: UploadFile = File(...), tags: str = Form('[]'), title: Optional[str] = Form(None), principal=Depends(authenticate), db: Session = Depends(get_db)):
    return save_upload(kb_id, file, tags, title, db, principal)


@router.put('/{kb_id}/documents/{doc_id}/file', status_code=202)
def replace_document(kb_id: str, doc_id: str, file: UploadFile = File(...), tags: str = Form('[]'), title: Optional[str] = Form(None), principal=Depends(authenticate), db: Session = Depends(get_db)):
    return save_upload(kb_id, file, tags, title, db, principal, doc_id)


@router.get('/{kb_id}/documents/{doc_id}/file')
def download_document(kb_id: str, doc_id: str, principal=Depends(authenticate), db: Session = Depends(get_db)):
    authorize(principal, [kb_id])
    doc, record = db.get(Document, doc_id), db.get(DocumentFile, doc_id)
    if not doc or doc.deleted or doc.knowledge_base_id != kb_id or not record:
        raise HTTPException(404, '文件文档不存在')
    path = stored_path(record.storage_path)
    if not path.is_file():
        raise HTTPException(404, '原文件已不可用')
    # 强制作为附件下载；不在服务端渲染上传的 Markdown/HTML 内容。
    return FileResponse(path, media_type='application/octet-stream', filename=record.filename)
