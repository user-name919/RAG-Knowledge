from copy import deepcopy
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.chunks import qa_chunks
from app.config import get_settings
from app.embedding import EmbeddingClient, EmbeddingError
from app.main import valid_candidates
from app.models import Document, IndexTask, QAPair, utcnow
from app.search import SearchStore, SearchError, fuse
from app.worker import claim_task, process_task


class FakeEmbedding:
    def embed(self, texts, query=False):
        return [[1.0, 0.0] for t in texts]


class FakeStore:
    def __init__(self):
        self.records = {}

    def ensure_index(self):
        pass

    def put_chunks(self, chunks, vectors):
        self.records.update({c['id']: deepcopy(c) for c in chunks})

    def delete_qa(self, qa_id, before_version=None, exact_version=None):
        for key, row in list(self.records.items()):
            if row['qa_id'] != qa_id:
                continue
            if before_version is not None and row['version'] >= before_version:
                continue
            if exact_version is not None and row['version'] != exact_version:
                continue
            del self.records[key]


def seed(client, external_id='one'):
    kb = client.post('/v1/knowledge_bases', json={'name': '教学'}).json()['id']
    doc = client.post(f'/v1/knowledge_bases/{kb}/documents', json={'title': '作业指南'}).json()['id']
    path = f'/v1/knowledge_bases/{kb}/documents/{doc}'
    payload = {'qa_pairs': [{'external_id': external_id, 'question': '学生看不到作业怎么办？', 'answer': '确认已经发布并选择正确班级。', 'tags': ['老师']}]}
    response = client.post(path + '/qa_pairs/batch_create', json=payload)
    assert response.status_code == 202, response.text
    return kb, doc, path, response.json()['data'][0], payload


def test_create_retry_is_idempotent_and_conflict_atomic(client, factory):
    _, _, path, first, payload = seed(client)
    retry = client.post(path + '/qa_pairs/batch_create', json=payload).json()['data'][0]
    assert retry['id'] == first['id'] and retry['reused']
    conflict = deepcopy(payload)
    conflict['qa_pairs'][0]['answer'] = '不同答案'
    conflict['qa_pairs'].insert(0, {'external_id': 'should-rollback', 'question': '其他问题', 'answer': '其他答案'})
    assert client.post(path + '/qa_pairs/batch_create', json=conflict).status_code == 409
    with factory() as db:
        assert len(db.scalars(select(QAPair)).all()) == 1
        assert len(db.scalars(select(IndexTask)).all()) == 1


def test_update_hides_stale_content_and_delete_is_immediate(client, factory):
    kb, doc, path, row, _ = seed(client)
    store = FakeStore()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    assert client.get(path + '/index_status').json()['data'][0]['index_status'] == 'ready'
    old = list(store.records.values())
    with factory() as db:
        assert len(valid_candidates(db, old, [kb], [doc], ['老师'])) == 1
    updated = client.put(path + '/qa_pairs/' + row['id'], json={'question': '学生看不到作业怎么办？', 'answer': '新版规则：检查发布时间。', 'tags': ['老师']})
    assert updated.status_code == 200
    with factory() as db:
        assert valid_candidates(db, old, [kb], [], []) == []
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    assert {c['version'] for c in store.records.values()} == {2}
    assert client.delete(path + '/qa_pairs/' + row['id']).status_code == 200
    with factory() as db:
        assert valid_candidates(db, list(store.records.values()), [kb], [], []) == []
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    assert store.records == {}


def test_edit_during_embedding_does_not_publish_old_revision(client, factory):
    _, _, path, row, _ = seed(client)
    store = FakeStore()

    class ConcurrentEdit(FakeEmbedding):
        def embed(self, texts, query=False):
            assert client.put(path + '/qa_pairs/' + row['id'], json={'question': '修订问题', 'answer': '修订答案'}).status_code == 200
            return super().embed(texts)

    process_task(*claim_task(factory), factory=factory, embedder=ConcurrentEdit(), store=store)
    assert store.records == {}
    with factory() as db:
        assert db.get(IndexTask, row['task_id']).status == 'superseded'
        assert db.get(QAPair, row['id']).indexed_version == 0


def test_failed_bulk_retry_does_not_duplicate_chunks(client, factory):
    _, _, _, row, _ = seed(client)

    class FailOnce(FakeStore):
        def put_chunks(self, chunks, vectors):
            super().put_chunks(chunks, vectors)
            if not hasattr(self, 'failed'):
                self.failed = True
                raise SearchError('partial write')

    store = FailOnce()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    with factory() as db:
        task = db.get(IndexTask, row['task_id'])
        assert task.status == 'pending'
        task.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    assert len(store.records) == 1


def test_exhausted_task_manual_retry_and_lease_recovery(client, factory):
    _, _, path, row, _ = seed(client)
    first = claim_task(factory)
    with factory() as db:
        task = db.get(IndexTask, row['task_id'])
        task.lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
    second = claim_task(factory)
    assert second[0] == first[0] and second[1] != first[1]
    with factory() as db:
        task = db.get(IndexTask, row['task_id'])
        task.status = 'failed'
        db.commit()
    assert client.post(path + '/retry_failed').json()['retried'] == 1
    assert claim_task(factory) is not None


def test_scoped_keys_cannot_cross_kbs_or_write(client):
    kb, _, path, _, payload = seed(client)
    other = client.post('/v1/knowledge_bases', json={'name': 'restricted'}).json()['id']
    key = client.post('/v1/api_keys', json={'name': 'teacher', 'knowledge_base_ids': [kb]}).json()
    headers = {'api-key': key['api_key']}
    assert client.get(path + '/qa_pairs', headers=headers).status_code == 200
    assert client.post(path + '/qa_pairs/batch_create', json=payload, headers=headers).status_code == 403
    assert client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb, other], 'query': 'test'}, headers=headers).status_code == 403
    assert client.post('/v1/knowledge_bases', json={'name': 'no'}, headers=headers).status_code == 403
    assert client.delete('/v1/api_keys/' + key['id']).status_code == 200
    assert client.get(path + '/qa_pairs', headers=headers).status_code == 401
    assert client.get('/v1/knowledge_bases', headers={'api-key': ''}).status_code == 401


def test_db_validation_checks_scope_and_tags(client, factory):
    kb, doc, _, _, _ = seed(client)
    store = FakeStore()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    rows = list(store.records.values())
    with factory() as db:
        assert valid_candidates(db, rows, ['other'], [], []) == []
        assert valid_candidates(db, rows, [kb], ['other'], []) == []
        assert valid_candidates(db, rows, [kb], [doc], ['运维']) == []
        tampered = deepcopy(rows)
        tampered[0]['document_id'] = 'other'
        assert valid_candidates(db, tampered, [kb], [], []) == []


def test_document_delete_invalidates_all_chunks(client, factory):
    kb, _, path, _, _ = seed(client)
    store = FakeStore()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    client.delete(path)
    with factory() as db:
        assert valid_candidates(db, list(store.records.values()), [kb], [], []) == []
    assert client.get(path + '/qa_pairs').status_code == 404


def test_unicode_chunking_is_lossless_and_bounded(client, factory):
    _, doc_id, _, row, _ = seed(client)
    with factory() as db:
        qa = db.get(QAPair, row['id'])
        qa.answer = '中文😀\nEnglish 配置项 TASK_403 ' * 200
        chunks = qa_chunks(qa, db.get(Document, doc_id), get_settings())
        assert ''.join(c['content'].split('\n答：', 1)[1] for c in chunks) == qa.answer
        assert all(len(c['embedding_text'].encode()) <= 480 for c in chunks)
        assert len({c['id'] for c in chunks}) == len(chunks)


def test_fusion_missing_branch_and_threshold(client):
    def hit(id, score):
        return {'_id': id, '_score': score, '_source': {'id': id}}
    rows = fuse([hit('semantic', 0.9), hit('both', 0.8)], [hit('both', 8), hit('keyword', 80)])
    assert [r['id'] for r in rows] == ['both', 'semantic', 'keyword']
    assert rows[0]['score'] == pytest.approx(.74)
    assert rows[1]['keyword_score'] == 0
    assert rows[2]['score'] < .2


def test_recall_filters_stale_results_and_applies_threshold(client, factory, monkeypatch):
    kb, _, _, _, _ = seed(client)
    store = FakeStore()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    row = list(store.records.values())[0]
    hit = {'_id': row['id'], '_score': .9, '_source': row}
    monkeypatch.setattr(EmbeddingClient, 'embed', FakeEmbedding().embed)
    monkeypatch.setattr(SearchStore, 'search', lambda *args: ([hit], []))
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': '孩子怎么没看到题'})
    assert r.status_code == 200 and r.json()['total'] == 1
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': '孩子怎么没看到题', 'retrieval_options': {'score_threshold': .8}})
    assert r.json()['total'] == 0


def test_provider_failure_returns_503_not_empty_success(client, monkeypatch):
    kb, _, _, _, _ = seed(client)
    def fail(*args, **kwargs):
        raise EmbeddingError('Embedding provider HTTP 429')
    monkeypatch.setattr(EmbeddingClient, 'embed', fail)
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': 'test'})
    assert r.status_code == 503


def test_invalid_weights_and_long_queries_rejected(client):
    kb, _, _, _, _ = seed(client)
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': 'test', 'weights': {'vector_setting': {'vector_weight': .8}, 'keyword_setting': {'vector_weight': .8}}})
    assert r.status_code == 422
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': '长' * 300})
    assert r.status_code == 422


def test_external_provider_contract(monkeypatch):
    import httpx
    from app.config import Settings
    actual_client = httpx.Client
    calls = []
    def handler(request):
        import json
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1., 0.]} for i in reversed(range(len(payload['input'])))]})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))
    settings = Settings(_env_file=None, embedding_api_key='test-only', embedding_dimensions=2)
    vectors = EmbeddingClient(settings).embed(['问题1', '问题2'], query=True)
    assert len(vectors) == 2
    assert calls[0]['input'][0].startswith(settings.embedding_query_prefix)
    assert calls[0]['model'] == 'BAAI/bge-large-zh-v1.5'


def test_failed_task_stops_at_retry_limit(client, factory):
    _, _, path, row, _ = seed(client)
    with factory() as db:
        task = db.get(IndexTask, row['task_id'])
        task.attempts = get_settings().task_max_attempts
        db.commit()
    assert claim_task(factory) is None
    assert client.get(path + '/index_status').json()['data'][0]['index_status'] == 'failed'


def test_old_lease_cannot_publish(client, factory):
    _, _, _, row, _ = seed(client)
    claim = claim_task(factory)
    with factory() as db:
        task = db.get(IndexTask, row['task_id'])
        task.lease_token = 'new-owner'
        db.commit()
    store = FakeStore()
    process_task(*claim, factory=factory, embedder=FakeEmbedding(), store=store)
    assert not store.records
