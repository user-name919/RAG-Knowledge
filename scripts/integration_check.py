# 真实 PostgreSQL/pgvector 集成验证。只替换 Embedding 输出，不调用云模型。
# 使用专属测试知识库与索引并在 finally 清理；运行前应停止常驻 Worker，避免争抢测试任务。

"""Real PostgreSQL + real pgvector, synthetic vectors. No external model call or quality claim."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import time
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from app.config import get_settings
from app.db import SessionLocal
from app.embedding import EmbeddingClient
from app.main import app
from app.models import APIKey, Document, IndexTask, KnowledgeBase, QAPair
from app.search import SearchStore
from app.worker import claim_task, process_task

settings = get_settings()
# 暂存默认索引名，后面所有 pgvector 操作切换到本次随机命名的测试索引。
old_index = settings.retrieval_collection
settings.retrieval_collection = 'team-rag-integration-' + uuid4().hex
original_embed = EmbeddingClient.embed
kb_ids = []


# 生成固定测试向量以验证索引维度和传输链路，不测试真实语义区分能力。
def fixture_vectors(self, texts, query=False):
    # Orthogonal fixture vectors test transport, index dimensions, filtering and lifecycle only.
    return [[1.0] + [0.0] * (settings.embedding_dimensions - 1) for t in texts]


# 仅当前测试进程替换模型方法；finally 恢复，避免后续调用误用测试向量。
EmbeddingClient.embed = fixture_vectors
try:
    with TestClient(app, headers={'api-key': settings.admin_api_key}) as client:
        # 调用测试客户端并在 HTTP 错误时停止，保留路径和截断后的错误信息用于排查。
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f'{method} {path}: HTTP {response.status_code} {response.text[:300]}')
            return response.json()

        kb = request('POST', '/v1/knowledge_bases', json={'name': 'integration-fixture'})['id']
        kb_ids.append(kb)
        doc = request('POST', f'/v1/knowledge_bases/{kb}/documents', json={'title': '测试文档', 'source_uri': 'fixture://guide'})['id']
        path = f'/v1/knowledge_bases/{kb}/documents/{doc}'
        payload = {'qa_pairs': [
            {'external_id': 'a', 'question': '学生看不到作业', 'answer': '检查目标班级。', 'tags': ['老师']},
            {'external_id': 'b', 'question': 'TASK_403 怎么处理', 'answer': '检查账号权限。', 'tags': ['运维']},
        ]}
        batch = request('POST', path + '/qa_pairs/batch_create', json=payload)['data']
        retry = request('POST', path + '/qa_pairs/batch_create', json=payload)['data']
        assert [q['id'] for q in retry] == [q['id'] for q in batch]
        # Scope workers to the two fixture tasks; never consume user tasks.
        # Test concurrent PostgreSQL SKIP LOCKED claims against a filtered session subclass.
        from sqlalchemy.orm import Session, sessionmaker
        from sqlalchemy import event
        # 为集成任务领取建立专属会话类型，限定测试的查询范围，不改变生产 SessionLocal。
        class FixtureSession(Session):
            pass
        # 仅给 IndexTask 查询加测试 QA 范围，防止脚本领取已有业务任务。
        @event.listens_for(FixtureSession, 'do_orm_execute')
        def restrict_tasks(state):
            if state.is_select and any(d.get('entity') is IndexTask for d in state.statement.column_descriptions):
                state.statement = state.statement.where(IndexTask.qa_id.in_([q['id'] for q in batch]))
        fixture_factory = sessionmaker(bind=SessionLocal.kw['bind'], class_=FixtureSession, expire_on_commit=False)
        # 短暂轮询等待任务可领取；PostgreSQL DATETIME 精度可能让当前时间被舍入到下一秒。
        def await_claim():
            # PostgreSQL DATETIME has second precision; immediate eligibility can round up.
            for _ in range(30):
                claimed = claim_task(fixture_factory)
                if claimed:
                    return claimed
                time.sleep(.1)
            raise AssertionError('Fixture task not claimable within 3 seconds')
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: await_claim(), range(2)))
        assert all(claims) and len({claim[0] for claim in claims}) == 2
        for claim in claims:
            process_task(*claim, factory=fixture_factory)
        states = request('GET', path + '/index_status')['data']
        assert all(s['index_status'] == 'ready' for s in states), states
        # 以错误码加标签检索，要求结果同时包含非零关键词和向量分数，验证两条分支确实运行。
        body = {'knowledge_base_ids': [kb], 'document_ids': [doc], 'query': 'TASK_403', 'retrieval_options': {'tags': ['运维']}}
        result = request('POST', '/v1/knowledge_bases/recall', json=body)
        assert result['total'] == 1, result
        assert result['data'][0]['qa_id'] == batch[1]['id']
        assert result['data'][0]['keyword_score'] > 0 and result['data'][0]['vector_score'] > 0
        # 中文词元召回、相邻错误码不误匹配，以及同库事务回滚。
        store = SearchStore()
        vector = fixture_vectors(None, ['query'])[0]
        _, chinese = store.search('学生作业', vector, [kb], [doc], [], 40)
        assert any(hit['_source']['qa_id'] == batch[0]['id'] for hit in chinese)
        _, wrong_code = store.search('TASK_404', vector, [kb], [doc], [], 40)
        assert wrong_code == []
        before = store.count()
        with SessionLocal() as transaction:
            store.delete_qa(batch[0]['id'], db=transaction)
            transaction.rollback()
        assert store.count() == before
        key = request('POST', '/v1/api_keys', json={'name': 'integration-reader', 'knowledge_base_ids': [kb]})
        denied = client.post('/v1/knowledge_bases/recall', headers={'api-key': key['api_key']}, json={'knowledge_base_ids': ['other'], 'query': 'test'})
        assert denied.status_code == 403
        qa_url = path + '/qa_pairs/' + batch[1]['id']
        request('PUT', qa_url, json={'question': 'TASK_403 怎么处理', 'answer': '新规则：检查授课关系。', 'tags': ['运维']})
        assert request('POST', '/v1/knowledge_bases/recall', json=body)['total'] == 0
        process_task(*await_claim(), factory=fixture_factory)
        current = request('POST', '/v1/knowledge_bases/recall', json=body)['data']
        assert len(current) == 1 and current[0]['version'] == 2 and '新规则' in current[0]['content']
        request('DELETE', qa_url)
        assert request('POST', '/v1/knowledge_bases/recall', json=body)['total'] == 0
        process_task(*await_claim(), factory=fixture_factory)
        assert SearchStore().count() == 1
        print('PASS: real PostgreSQL + pgvector; concurrent claims, idempotency, indexing, hybrid scores, filters, ACL, update and delete.')
        print('Synthetic vectors only: real Embedding connectivity and semantic quality remain unverified.')
finally:
    EmbeddingClient.embed = original_embed
    # 按外键依赖顺序清理任务、QA、文档及知识库；只清理本次创建的记录。
    with SessionLocal() as db, db.begin():
        docs = list(db.scalars(select(Document.id).where(Document.knowledge_base_id.in_(kb_ids))))
        qas = list(db.scalars(select(QAPair.id).where(QAPair.document_id.in_(docs))))
        db.execute(delete(IndexTask).where(IndexTask.qa_id.in_(qas)))
        db.execute(delete(QAPair).where(QAPair.id.in_(qas)))
        db.execute(delete(Document).where(Document.id.in_(docs)))
        for key in db.scalars(select(APIKey)):
            if set(key.knowledge_base_ids).intersection(kb_ids):
                db.delete(key)
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids)))
    from sqlalchemy import text
    with SessionLocal() as db, db.begin():
        db.execute(text('DELETE FROM knowledge_chunks WHERE collection=:collection'), {'collection': settings.retrieval_collection})
        db.execute(text('DELETE FROM retrieval_metadata WHERE collection=:collection'), {'collection': settings.retrieval_collection})
    settings.retrieval_collection = old_index
