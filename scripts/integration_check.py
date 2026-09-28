"""Real MySQL + real ES, synthetic vectors. No external model call or quality claim."""
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
old_index = settings.es_index
settings.es_index = 'team-rag-integration-' + uuid4().hex
original_embed = EmbeddingClient.embed
kb_ids = []


def fixture_vectors(self, texts, query=False):
    # Orthogonal fixture vectors test transport, index dimensions, filtering and lifecycle only.
    return [[1.0] + [0.0] * (settings.embedding_dimensions - 1) for t in texts]


EmbeddingClient.embed = fixture_vectors
try:
    with TestClient(app, headers={'api-key': settings.admin_api_key}) as client:
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
        # Test concurrent MySQL SKIP LOCKED claims against a filtered session subclass.
        from sqlalchemy.orm import Session, sessionmaker
        from sqlalchemy import event
        class FixtureSession(Session):
            pass
        @event.listens_for(FixtureSession, 'do_orm_execute')
        def restrict_tasks(state):
            if state.is_select and any(d.get('entity') is IndexTask for d in state.statement.column_descriptions):
                state.statement = state.statement.where(IndexTask.qa_id.in_([q['id'] for q in batch]))
        fixture_factory = sessionmaker(bind=SessionLocal.kw['bind'], class_=FixtureSession, expire_on_commit=False)
        def await_claim():
            # MySQL DATETIME has second precision; immediate eligibility can round up.
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
        body = {'knowledge_base_ids': [kb], 'document_ids': [doc], 'query': 'TASK_403', 'retrieval_options': {'tags': ['运维']}}
        result = request('POST', '/v1/knowledge_bases/recall', json=body)
        assert result['total'] == 1, result
        assert result['data'][0]['qa_id'] == batch[1]['id']
        assert result['data'][0]['keyword_score'] > 0 and result['data'][0]['vector_score'] > 0
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
        assert SearchStore().request('GET', '/' + settings.es_index + '/_count')['count'] == 1
        print('PASS: real MySQL + ES; concurrent claims, idempotency, indexing, hybrid scores, filters, ACL, update and delete.')
        print('Synthetic vectors only: real Embedding connectivity and semantic quality remain unverified.')
finally:
    EmbeddingClient.embed = original_embed
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
    try:
        SearchStore().request('DELETE', '/' + settings.es_index)
    finally:
        settings.es_index = old_index
