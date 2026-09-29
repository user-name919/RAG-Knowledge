# 上传与文件索引回归测试。文件都在 tmp_path 中生成，不使用用户真实资料。
from io import BytesIO
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from sqlalchemy import select
from app.config import get_settings
from app.documents import stored_path
from app.file_indexer import process_file_task, parse_file, DocumentError
from app.main import valid_candidates
from app.models import DocumentFile, FileIndexTask, Document
from app.tokenization import token_count, text_chunks
from app.worker import claim_task
from tests.test_knowledge import FakeEmbedding, FakeStore
import pytest


class FileStore(FakeStore):
    # 模拟仅删除文档片段，不清理同节点的 QA。
    def delete_document(self, document_id, before_version=None, exact_version=None):
        for key, row in list(self.records.items()):
            if row.get('type') != 'document' or row['document_id'] != document_id:
                continue
            if before_version is not None and row['version'] >= before_version:
                continue
            if exact_version is not None and row['version'] != exact_version:
                continue
            del self.records[key]


@pytest.fixture
def uploaded(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), 'upload_dir', str(tmp_path))
    kb = client.post('/v1/knowledge_bases', json={'name': '文档测试'}).json()['id']
    def send(name='guide.md', content='# 作业指南\n\n## 发布范围\n只对目标班级可见。'.encode(), **kwargs):
        return client.post(f'/v1/knowledge_bases/{kb}/documents/upload', files={'file': (name, content)}, **kwargs)
    return kb, send


def process(factory, store, embedder=None):
    claim = claim_task(factory, FileIndexTask)
    assert claim
    process_file_task(*claim, factory=factory, embedder=embedder or FakeEmbedding(), store=store)


# 生成一页确实含有文本对象的 PDF，不依赖任何字体或外部 PDF 工具。
def pdf_bytes(text='Assignment is visible to the target class.', blank=False):
    writer = PdfWriter()
    page = writer.add_blank_page(400, 400)
    if not blank:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 20 350 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def test_upload_index_replace_delete(client, factory, uploaded):
    kb, send = uploaded
    response = send()
    assert response.status_code == 202, response.text
    doc_id = response.json()['id']; url = f'/v1/knowledge_bases/{kb}/documents/{doc_id}'
    store = FileStore(); process(factory, store)
    status = client.get(url + '/index_status').json()['file']
    assert status['index_status'] == 'ready' and status['chunk_count'] >= 1
    assert any('发布范围' in c['source_location'] for c in store.records.values())
    with factory() as db:
        assert valid_candidates(db, list(store.records.values()), [kb], [doc_id], [])
    file = client.get(url + '/file')
    assert file.status_code == 200 and '作业指南' in file.content.decode()
    replaced = client.put(url + '/file', files={'file': ('guide.txt', '新规则：必须先发布。'.encode())})
    assert replaced.status_code == 202 and replaced.json()['version'] == 2
    with factory() as db:
        assert valid_candidates(db, list(store.records.values()), [kb], [], []) == []
    process(factory, store)
    assert all(c['version'] == 2 for c in store.records.values())
    # 完全相同的替换复用版本，不再次向量化。
    same = client.put(url + '/file', files={'file': ('guide.txt', '新规则：必须先发布。'.encode())})
    assert same.json()['reused'] is True
    assert client.delete(url).status_code == 200
    assert client.get(url + '/file').status_code == 404
    with factory() as db:
        assert valid_candidates(db, list(store.records.values()), [kb], [], []) == []
        original = stored_path(db.get(DocumentFile, doc_id).storage_path)
    process(factory, store)
    assert not store.records and not original.exists()
    assert client.get(url + '/index_status').json()['file']['index_status'] == 'deleted'


@pytest.mark.parametrize('name,data,status', [('bad.exe', b'abcd', 415), ('empty.txt', b'', 422), ('bad.txt', b'\xff', 422), ('null.txt', b'hello\x00', 422), ('fake.pdf', b'not pdf', 422)])
def test_reject_invalid_uploads(uploaded, name, data, status):
    _, send = uploaded
    assert send(name, data).status_code == status


def test_size_and_tag_validation(uploaded, monkeypatch):
    _, send = uploaded
    monkeypatch.setattr(get_settings(), 'max_upload_bytes', 100)
    assert send('big.txt', b'a' * 101).status_code == 413
    assert send(data={'tags': '[null]'}).status_code == 422


def test_path_traversal_and_authorization(client, factory, uploaded, tmp_path):
    kb, send = uploaded
    record = send('../../escape.md', b'# Valid').json()
    with factory() as db:
        saved = db.get(DocumentFile, record['id'])
        assert saved.filename == 'escape.md'
        assert str(stored_path(saved.storage_path)).startswith(str(tmp_path))
    with pytest.raises(ValueError):
        stored_path('../escape')
    other = client.post('/v1/knowledge_bases', json={'name': 'other'}).json()['id']
    key = client.post('/v1/api_keys', json={'name': 'reader', 'knowledge_base_ids': [other]}).json()['api_key']
    headers = {'api-key': key}
    assert client.get(f'/v1/knowledge_bases/{kb}/documents/{record["id"]}/file', headers=headers).status_code == 403
    assert client.post(f'/v1/knowledge_bases/{other}/documents/upload', headers=headers, files={'file': ('a.md', b'# test')}).status_code == 403


def test_pdf_page_source_and_scanned_failure(client, factory, uploaded):
    kb, send = uploaded
    doc = send('guide.pdf', pdf_bytes()).json()['id']
    store = FileStore(); process(factory, store)
    assert all(c['page_number'] == 1 for c in store.records.values())
    assert any('target class' in c['content'] for c in store.records.values())
    blank = send('scanned.pdf', pdf_bytes(blank=True)).json()['id']
    process(factory, store)
    status = client.get(f'/v1/knowledge_bases/{kb}/documents/{blank}/index_status').json()['file']
    assert status['index_status'] == 'failed' and 'OCR' in status['last_error']


def test_concurrent_replacement_never_publishes_old_file(client, factory, uploaded):
    kb, send = uploaded
    doc = send().json()['id']; url = f'/v1/knowledge_bases/{kb}/documents/{doc}'
    class ConcurrentEdit(FakeEmbedding):
        def embed(self, texts, query=False):
            result = client.put(url + '/file', files={'file': ('new.txt', b'Updated instructions')})
            assert result.status_code == 202
            return super().embed(texts)
    store = FileStore(); process(factory, store, ConcurrentEdit())
    assert store.records == {}
    with factory() as db:
        assert db.get(DocumentFile, doc).indexed_version == 0
        assert db.scalar(select(FileIndexTask).where(FileIndexTask.document_id == doc, FileIndexTask.version == 1)).status == 'superseded'


def test_token_chunks_preserve_text_and_code_headings(tmp_path):
    text = ('长文档 English words 😀\n\n' * 200)
    chunks = list(text_chunks(text, prefix='资料标题\n'))
    assert ''.join(chunks) == text and all(token_count('资料标题\n' + x) <= 480 for x in chunks)
    p = tmp_path / 'code.md'; p.write_text('# 标题\n```python\n# 不是标题\n```\n## 章节\n正文')
    sections, _ = parse_file(p, get_settings())
    assert len(sections) == 2 and '不是标题' in sections[0][0]


def test_model_failure_then_manual_retry(client, factory, uploaded):
    from app.embedding import EmbeddingError
    kb, send = uploaded
    doc = send().json()['id']
    class Broken(FakeEmbedding):
        def embed(self, *args, **kwargs): raise EmbeddingError('Embedding provider HTTP 503')
    store = FileStore(); process(factory, store, Broken())
    with factory() as db:
        task = db.scalar(select(FileIndexTask).where(FileIndexTask.document_id == doc))
        assert task.status == 'pending'
        task.status = 'failed'; db.commit()
    assert client.post(f'/v1/knowledge_bases/{kb}/documents/{doc}/retry_failed').json()['retried'] == 1
    process(factory, store)
    assert store.records
