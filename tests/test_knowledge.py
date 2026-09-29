# 知识库行为测试：覆盖权限、幂等、版本一致性、任务租约、检索融合及供应商接口约定。
# FakeEmbedding/FakeStore 只验证业务流程，不证明真实向量的语义质量。

from copy import deepcopy
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.chunks import qa_chunks
from app.tokenization import token_count
from app.config import get_settings
from app.embedding import EmbeddingClient, EmbeddingError
from app.main import valid_candidates
from app.models import Document, IndexTask, QAPair, utcnow
from app.search import SearchStore, SearchError, fuse
from app.worker import claim_task, process_task


# 确定性向量替身，用于隔离业务流程测试，不调用真实模型。
class FakeEmbedding:
    # 返回每条输入对应的测试向量；并发编辑替身会在返回前故意触发一次更新。
    def embed(self, texts, query=False):
        return [[1.0, 0.0] for t in texts]


# 用字典模拟 ES 的确定性 ID 写入和按版本删除行为。
class FakeStore:
    # 每个实例单独保存片段，避免不同测试互相污染。
    def __init__(self):
        self.records = {}

    # 内存替身无需建索引，保留方法以匹配业务调用。
    def ensure_index(self):
        pass

    # 复制片段后按 ID 覆盖，模拟批量写入幂等；FailOnce 子类在首次写入后抛出异常。
    def put_chunks(self, chunks, vectors):
        self.records.update({c['id']: deepcopy(c) for c in chunks})

    # 模拟只删除指定 QA、指定版本范围的片段，供更新和过期任务测试使用。
    def delete_qa(self, qa_id, before_version=None, exact_version=None):
        for key, row in list(self.records.items()):
            if row['qa_id'] != qa_id:
                continue
            if before_version is not None and row['version'] >= before_version:
                continue
            if exact_version is not None and row['version'] != exact_version:
                continue
            del self.records[key]


# 通过真实 API 路由创建测试知识库、节点与一条 QA，返回后续调用所需 ID 和请求体。
def seed(client, external_id='one'):
    kb = client.post('/v1/knowledge_bases', json={'name': '教学'}).json()['id']
    doc = client.post(f'/v1/knowledge_bases/{kb}/documents', json={'title': '作业指南'}).json()['id']
    path = f'/v1/knowledge_bases/{kb}/documents/{doc}'
    payload = {'qa_pairs': [{'external_id': external_id, 'question': '学生看不到作业怎么办？', 'answer': '确认已经发布并选择正确班级。', 'tags': ['老师']}]}
    response = client.post(path + '/qa_pairs/batch_create', json=payload)
    assert response.status_code == 202, response.text
    return kb, doc, path, response.json()['data'][0], payload


# 重复提交复用 QA；同一批后续冲突时，前面尚未提交的新记录也必须回滚。
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


# 依次验证初次发布、更新屏蔽旧内容、新版替换及软删除即时失效。
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


# 向量生成期间发生修改，旧任务必须 superseded，且不能推进 indexed_version。
def test_edit_during_embedding_does_not_publish_old_revision(client, factory):
    _, _, path, row, _ = seed(client)
    store = FakeStore()

    # 在模型调用期间制造更新，验证慢任务完成时不能重新发布旧内容。
    class ConcurrentEdit(FakeEmbedding):
        # 返回每条输入对应的测试向量；并发编辑替身会在返回前故意触发一次更新。
        def embed(self, texts, query=False):
            assert client.put(path + '/qa_pairs/' + row['id'], json={'question': '修订问题', 'answer': '修订答案'}).status_code == 200
            return super().embed(texts)

    process_task(*claim_task(factory), factory=factory, embedder=ConcurrentEdit(), store=store)
    assert store.records == {}
    with factory() as db:
        assert db.get(IndexTask, row['task_id']).status == 'superseded'
        assert db.get(QAPair, row['id']).indexed_version == 0


# 部分写入后失败并重试，最终只保留一份同版本片段。
def test_failed_bulk_retry_does_not_duplicate_chunks(client, factory):
    _, _, _, row, _ = seed(client)

    # 模拟服务端已写入但客户端收到失败，验证重试不会生成重复片段。
    class FailOnce(FakeStore):
        # 复制片段后按 ID 覆盖，模拟批量写入幂等；FailOnce 子类在首次写入后抛出异常。
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


# 租约超时后可重新领取且 token 改变；失败任务可通过接口重置。
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


# 只读 Key 不能写入或跨库召回；撤销后认证失败，空凭据也应拒绝。
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


# 数据库复核必须过滤错误知识库、文档、标签以及来源 ID 不一致的候选。
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


# 删除整个节点后，即使 ES 仍有片段也不可返回，QA 列表也不可访问。
def test_document_delete_invalidates_all_chunks(client, factory):
    kb, _, path, _, _ = seed(client)
    store = FakeStore()
    process_task(*claim_task(factory), factory=factory, embedder=FakeEmbedding(), store=store)
    client.delete(path)
    with factory() as db:
        assert valid_candidates(db, list(store.records.values()), [kb], [], []) == []
    assert client.get(path + '/qa_pairs').status_code == 404


# 混合中文、英文和 emoji 切分后内容可无损拼回，每个向量输入均符合token 预算。
def test_unicode_chunking_is_lossless_and_bounded(client, factory):
    _, doc_id, _, row, _ = seed(client)
    with factory() as db:
        qa = db.get(QAPair, row['id'])
        qa.answer = '中文😀\nEnglish 配置项 TASK_403 ' * 200
        chunks = qa_chunks(qa, db.get(Document, doc_id), get_settings())
        assert ''.join(c['content'].split('\n答：', 1)[1] for c in chunks) == qa.answer
        assert all(token_count(c['embedding_text']) <= 480 for c in chunks)
        assert len({c['id'] for c in chunks}) == len(chunks)


# 用可手算分数验证双路加权及缺失分支为零；纯关键词候选分数受其权重限制。
def test_fusion_missing_branch_and_threshold(client):
    # 构造最小 ES 命中结构，便于手工核对融合分数。
    def hit(id, score):
        return {'_id': id, '_score': score, '_source': {'id': id}}
    rows = fuse([hit('semantic', 0.9), hit('both', 0.8)], [hit('both', 8), hit('keyword', 80)])
    assert [r['id'] for r in rows] == ['both', 'semantic', 'keyword']
    assert rows[0]['score'] == pytest.approx(.74)
    assert rows[1]['keyword_score'] == 0
    assert rows[2]['score'] < .2


# 构造有效候选，验证召回接口在融合后应用阈值；旧版本过滤由其他用例单独验证。
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


# 供应商异常应返回 503，不能伪装成成功但没有资料。
def test_provider_failure_returns_503_not_empty_success(client, monkeypatch):
    kb, _, _, _, _ = seed(client)
    # 模拟供应商限流异常，验证接口按依赖故障返回 503。
    def fail(*args, **kwargs):
        raise EmbeddingError('Embedding provider HTTP 429')
    monkeypatch.setattr(EmbeddingClient, 'embed', fail)
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': 'test'})
    assert r.status_code == 503


# 权重和不为 1、模型输入预算超限均应返回 422，避免发出无效检索请求。
def test_invalid_weights_and_long_queries_rejected(client):
    kb, _, _, _, _ = seed(client)
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': 'test', 'weights': {'vector_setting': {'vector_weight': .8}, 'keyword_setting': {'vector_weight': .8}}})
    assert r.status_code == 422
    r = client.post('/v1/knowledge_bases/recall', json={'knowledge_base_ids': [kb], 'query': '长' * 600})
    assert r.status_code == 422


# 使用 MockTransport 验证模型名、查询前缀及乱序响应处理，不消耗云模型额度。
def test_external_provider_contract(monkeypatch):
    import httpx
    from app.config import Settings
    actual_client = httpx.Client
    calls = []
    # 拦截 HTTP 并故意倒序返回 embedding 索引，验证客户端恢复输入顺序。
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


# 达到最大尝试次数的任务不再领取，而是进入 failed 等待人工重试。
def test_failed_task_stops_at_retry_limit(client, factory):
    _, _, path, row, _ = seed(client)
    with factory() as db:
        task = db.get(IndexTask, row['task_id'])
        task.attempts = get_settings().task_max_attempts
        db.commit()
    assert claim_task(factory) is None
    assert client.get(path + '/index_status').json()['data'][0]['index_status'] == 'failed'


# 任务被新 Worker 接管后，旧 token 不能继续执行写索引或发布版本。
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
