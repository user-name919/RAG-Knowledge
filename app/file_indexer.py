# 文件解析与后台索引。格式错误立即标记失败，网络故障使用任务退避重试。
from datetime import timedelta
from pathlib import Path
import re
from pypdf import PdfReader
from sqlalchemy import select
from .config import get_settings
from .db import SessionLocal
from .documents import stored_path
from .embedding import EmbeddingClient, EmbeddingError
from .models import Document, DocumentFile, FileIndexTask, utcnow
from .search import SearchStore, SearchError
from .tokenization import text_chunks


class DocumentError(RuntimeError):
    """可以向用户展示的解析错误，不包含内部路径和凭据。"""


# 返回 (正文, 标题路径, PDF页码) 及警告。PDF按页解析，Markdown按标题层级组织。
def parse_file(path, settings):
    warnings, sections = [], []
    if path.suffix.lower() == '.pdf':
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise DocumentError('暂不支持加密 PDF，请解密后上传')
            if len(reader.pages) > 300:
                raise DocumentError('PDF 最多支持 300 页，请拆分后上传')
            blank = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                content = page.get_contents()
                if content and len(content.get_data()) > 20 * 1024 * 1024:
                    raise DocumentError('PDF 单页内容过大，请优化或拆分文件')
                text = page.extract_text() or ''
                total += len(text)
                if total > settings.max_document_chars:
                    raise DocumentError('提取文本超过 100 万字符，请拆分文件')
                if text.strip():
                    sections.append((text, '', number))
                else:
                    blank.append(number)
            if blank:
                warnings.append('以下页面未提取到文字（可能为空白或扫描图片）：' + ', '.join(map(str, blank)))
        except DocumentError:
            raise
        except Exception:
            raise DocumentError('PDF 解析失败，请检查文件是否损坏；扫描件需先进行 OCR') from None
    else:
        try:
            text = path.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeDecodeError):
            raise DocumentError('无法读取 UTF-8 文本，请重新上传') from None
        if len(text) > settings.max_document_chars:
            raise DocumentError('正文超过 100 万字符，请拆分文件')
        if path.suffix.lower() in ('.md', '.markdown'):
            headings, block, in_code = [], [], False
            for line in text.splitlines(keepends=True):
                if line.lstrip().startswith(('```', '~~~')):
                    in_code = not in_code
                heading = None if in_code else re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line.rstrip())
                if heading:
                    if ''.join(block).strip():
                        sections.append((''.join(block), ' / '.join(h for _, h in headings), None))
                    level = len(heading[1])
                    headings = [(l, h) for l, h in headings if l < level] + [(level, heading[2])]
                    block = [line]
                else:
                    block.append(line)
            if ''.join(block).strip():
                sections.append((''.join(block), ' / '.join(h for _, h in headings), None))
        else:
            sections = [(text, '', None)]
    if not any(text.strip() for text, _, _ in sections):
        raise DocumentError('未提取到有效正文；扫描版 PDF 暂不支持，请先 OCR 转成文本')
    return sections, warnings


def document_chunks(record, doc, settings):
    sections, warnings = parse_file(stored_path(record.storage_path), settings)
    chunks = []
    for text, heading, page in sections:
        prefix = doc.title[:80] + '\n' + heading[:80] + '\n'
        for part in text_chunks(text, prefix=prefix):
            if not part.strip():
                continue
            position = len(chunks)
            chunks.append({'id': f'file:{doc.id}:{record.version}:{position}', 'type': 'document',
                'knowledge_base_id': doc.knowledge_base_id, 'document_id': doc.id, 'version': record.version,
                'title': doc.title, 'content': part, 'embedding_text': prefix + part,
                'heading': heading, 'page_number': page, 'tags': record.tags,
                'source_uri': doc.source_uri, 'source_location': (f'第 {page} 页' if page else heading or '正文') + f' · 片段 {position + 1}',
                'updated_at': record.updated_at.isoformat() + 'Z', 'embedding_model': settings.embedding_model})
            if len(chunks) > settings.max_document_chunks:
                raise DocumentError('分片超过 1000 个，请拆分文件后上传')
    return chunks, warnings


# 每批外部调用前续租；租约所有权改变时，停止本任务，最终发布仍需要二次校验。
def renew(task_id, token, factory):
    with factory() as db, db.begin():
        task = db.scalar(select(FileIndexTask).where(FileIndexTask.id == task_id).with_for_update())
        if not task or task.status != 'running' or task.lease_token != token:
            return False
        task.lease_until = utcnow() + timedelta(seconds=get_settings().task_lease_seconds)
        return True


def process_file_task(task_id, token, factory=SessionLocal, embedder=None, store=None):
    settings, embedder, store = get_settings(), embedder or EmbeddingClient(), store or SearchStore()
    try:
        with factory() as db:
            task = db.get(FileIndexTask, task_id)
            if not task or task.status != 'running' or task.lease_token != token:
                return
            doc = db.get(Document, task.document_id)
            record = db.get(DocumentFile, doc.id)
            version, doc_id = task.version, doc.id
            valid = record.version == version and not doc.deleted
            chunks, warnings = document_chunks(record, doc, settings) if valid else ([], [])
        store.ensure_index()
        for start in range(0, len(chunks), 16):
            if not renew(task_id, token, factory):
                return
            batch = chunks[start:start + 16]
            vectors = embedder.embed([c['embedding_text'] for c in batch])
            if len(vectors) != len(batch):
                raise EmbeddingError('Embedding count mismatch')
            store.put_chunks(batch, vectors)
        # 与 API 一致：先锁文档，再锁扩展记录及任务，避免更新/删除与发布交错。
        with factory() as db, db.begin():
            doc = db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
            record = db.scalar(select(DocumentFile).where(DocumentFile.document_id == doc_id).with_for_update())
            task = db.scalar(select(FileIndexTask).where(FileIndexTask.id == task_id).with_for_update())
            if task.lease_token != token or task.status != 'running':
                return
            if record.version != version:
                store.delete_document(doc_id, exact_version=version)
                task.status = 'superseded'
            elif doc.deleted:
                store.delete_document(doc_id)
                stored_path(record.storage_path).unlink(missing_ok=True)
                record.indexed_version, record.chunk_count = version, 0
                task.status = 'done'
            else:
                store.delete_document(doc_id, before_version=version)
                record.indexed_version, record.chunk_count, record.warnings = version, len(chunks), warnings
                task.status = 'done'
            task.last_error, task.lease_until = None, None
    except Exception as exc:
        error = str(exc) if isinstance(exc, (DocumentError, EmbeddingError, SearchError)) else type(exc).__name__ + ': document indexing failed'
        with factory() as db, db.begin():
            task = db.scalar(select(FileIndexTask).where(FileIndexTask.id == task_id).with_for_update())
            if task and task.lease_token == token and task.status == 'running':
                task.last_error = error[:500]
                task.status = 'failed' if isinstance(exc, DocumentError) or task.attempts >= settings.task_max_attempts else 'pending'
                task.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** task.attempts * 5))
                task.lease_until = None
