# PostgreSQL 检索适配：向量、全文索引及业务数据共用数据库。
# 片段先暂存，Worker 校验版本后发布；清理旧片段与版本发布使用同一个事务。
import json
import math
import re
from contextlib import contextmanager
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .config import get_settings
from .db import engine


class SearchError(RuntimeError):
    """对外只暴露脱敏的检索错误，避免 SQL 参数携带正文或凭据。"""


def keyword_tokens(value):
    """中文采用重叠双字词元；英文、错误码、路径保留完整词项。

    将词项编码成 ASCII，避免 PostgreSQL 默认解析器拆开 TASK_403 等精确词。
    这是一套明确的基线分词规则，不宣称具备中文语义分词或 ES BM25 的效果。
    """
    result = []
    for word in re.findall(r'[\u3400-\u9fff]+|[a-zA-Z0-9_]+(?:[./:\-][a-zA-Z0-9_]+)*', value.lower()):
        terms = [word[i:i + 2] for i in range(len(word) - 1)] if re.fullmatch(r'[\u3400-\u9fff]{2,}', word) else [word]
        # PostgreSQL 词项最长 2046 字节；过长机器字符串不进入关键词路，原文和向量仍保留。
        result.extend('t' + term.encode().hex() for term in terms if len(term.encode()) <= 900)
    return result


class SearchStore:
    def __init__(self, settings=None, bind=None):
        self.settings = settings or get_settings()
        self.engine = bind or engine

    @contextmanager
    def connection(self, db=None):
        # Worker 传入业务会话时，不在这里提交；删除和 indexed_version 一起成功或回滚。
        try:
            if db is not None:
                yield db
            else:
                with self.engine.begin() as connection:
                    yield connection
        except SQLAlchemyError:
            raise SearchError('PostgreSQL search operation failed') from None

    def ensure_index(self):
        s = self.settings
        dims = s.embedding_dimensions
        if not 1 <= dims <= 2000:
            raise SearchError('HNSW vector dimensions must be between 1 and 2000')
        with self.connection() as conn:
            # 初始化锁防止 API 和多个 Worker 同时创建扩展、表及索引。
            conn.execute(text('SELECT pg_advisory_xact_lock(78124031)'))
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
            conn.execute(text('''CREATE TABLE IF NOT EXISTS retrieval_metadata (
                collection text PRIMARY KEY, model text NOT NULL, dimensions integer NOT NULL,
                schema_version integer NOT NULL)'''))
            conn.execute(text('''INSERT INTO retrieval_metadata VALUES (:collection, :model, :dims, 1)
                ON CONFLICT (collection) DO NOTHING'''), {'collection': s.retrieval_collection, 'model': s.embedding_model, 'dims': dims})
            row = conn.execute(text('SELECT model, dimensions, schema_version FROM retrieval_metadata WHERE collection=:collection'), {'collection': s.retrieval_collection}).one()
            if tuple(row) != (s.embedding_model, dims, 1):
                raise SearchError('Collection model/schema mismatch; migrate and reindex explicitly')
            # 维度来自经过整数范围校验的配置，不插入用户输入或 SQL 标识符。
            conn.execute(text(f'''CREATE TABLE IF NOT EXISTS knowledge_chunks (
                collection text NOT NULL, id text NOT NULL, knowledge_base_id text NOT NULL,
                document_id text NOT NULL, qa_id text, version integer NOT NULL,
                kind text NOT NULL, payload jsonb NOT NULL, tags jsonb NOT NULL,
                embedding vector({dims}) NOT NULL, keywords tsvector NOT NULL,
                PRIMARY KEY (collection, id))'''))
            actual = conn.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='knowledge_chunks'::regclass AND attname='embedding'")).scalar_one()
            if actual != f'vector({dims})':
                raise SearchError('Stored vector dimensions differ; use an explicit database migration')
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_chunks_vector ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_chunks_keywords ON knowledge_chunks USING gin (keywords)'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_chunks_scope ON knowledge_chunks (collection, knowledge_base_id, document_id)'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_chunks_qa ON knowledge_chunks (collection, qa_id)'))

    def count(self):
        with self.connection() as conn:
            return conn.execute(text('SELECT count(*) FROM knowledge_chunks WHERE collection=:collection'), {'collection': self.settings.retrieval_collection}).scalar_one()

    def put_chunks(self, chunks, vectors, db=None):
        if len(chunks) != len(vectors):
            raise SearchError('Embedding count mismatch')
        rows = []
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self.settings.embedding_dimensions or any(not math.isfinite(v) for v in vector) or not any(vector):
                raise SearchError('Invalid embedding dimensions or values')
            payload = {k: v for k, v in chunk.items() if k != 'embedding_text'}
            rows.append({'collection': self.settings.retrieval_collection, 'id': chunk['id'],
                'kb': chunk['knowledge_base_id'], 'doc': chunk['document_id'], 'qa': chunk.get('qa_id'),
                'version': chunk['version'], 'kind': chunk.get('type', 'qa'),
                'payload': json.dumps(payload, ensure_ascii=False), 'tags': json.dumps(chunk.get('tags', [])),
                'vector': json.dumps(vector),
                'question': ' '.join(keyword_tokens(chunk.get('question', '') + ' ' + chunk.get('stand_query', ''))),
                'title': ' '.join(keyword_tokens(chunk.get('title', '') + ' ' + chunk.get('heading', ''))),
                'content': ' '.join(keyword_tokens(chunk['content']))})
        if not rows:
            return
        with self.connection(db) as conn:
            # 确定性片段 ID + UPSERT 保证任务重试不重复；一批写入在同一事务提交。
            conn.execute(text('''INSERT INTO knowledge_chunks
                (collection,id,knowledge_base_id,document_id,qa_id,version,kind,payload,tags,embedding,keywords)
                VALUES (:collection,:id,:kb,:doc,:qa,:version,:kind,CAST(:payload AS jsonb),CAST(:tags AS jsonb),CAST(:vector AS vector),
                    setweight(to_tsvector('simple',:question),'A') || setweight(to_tsvector('simple',:title),'B') || setweight(to_tsvector('simple',:content),'D'))
                ON CONFLICT (collection,id) DO UPDATE SET payload=EXCLUDED.payload,tags=EXCLUDED.tags,
                    embedding=EXCLUDED.embedding,keywords=EXCLUDED.keywords'''), rows)

    def delete_qa(self, qa_id, before_version=None, exact_version=None, db=None):
        self._delete('qa_id=:target', qa_id, before_version, exact_version, db)

    def delete_document(self, document_id, before_version=None, exact_version=None, db=None):
        self._delete("document_id=:target AND kind='document'", document_id, before_version, exact_version, db)

    def _delete(self, condition, target, before_version, exact_version, db):
        params = {'collection': self.settings.retrieval_collection, 'target': target}
        if before_version is not None:
            condition += ' AND version<:version'
            params['version'] = before_version
        if exact_version is not None:
            condition += ' AND version=:version'
            params['version'] = exact_version
        with self.connection(db) as conn:
            conn.execute(text('DELETE FROM knowledge_chunks WHERE collection=:collection AND ' + condition), params)

    def search(self, query, vector, kb_ids, document_ids, tags, count):
        if len(vector) != self.settings.embedding_dimensions or any(not math.isfinite(v) for v in vector) or not any(vector):
            raise SearchError('Invalid query vector')
        params = {'collection': self.settings.retrieval_collection, 'kbs': kb_ids, 'docs': document_ids,
                  'tags': json.dumps(tags), 'vector': json.dumps(vector), 'count': count}
        # 在 LIMIT 之前校验已发布版本、删除状态和范围，避免失效片段挤占候选名额。
        condition = '''c.collection=:collection AND c.knowledge_base_id=ANY(:kbs)
            AND c.tags @> CAST(:tags AS jsonb)
            AND EXISTS (SELECT 1 FROM documents d WHERE d.id=c.document_id AND NOT d.deleted AND d.knowledge_base_id=c.knowledge_base_id)
            AND ((c.kind='qa' AND EXISTS (SELECT 1 FROM qa_pairs q WHERE q.id=c.qa_id AND q.document_id=c.document_id
                AND NOT q.deleted AND q.version=c.version AND q.indexed_version=c.version))
              OR (c.kind='document' AND EXISTS (SELECT 1 FROM document_files f WHERE f.document_id=c.document_id
                AND f.version=c.version AND f.indexed_version=c.version)))'''
        if document_ids:
            condition += ' AND c.document_id=ANY(:docs)'
        with self.connection() as conn:
            # 过滤后的 HNSW 迭代扫描，避免权限范围较小时只拿到少量候选。
            conn.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            conn.execute(text("SELECT set_config('hnsw.ef_search', :ef, true)"), {'ef': str(max(100, count))})
            semantic = conn.execute(text(f'''SELECT c.id,c.payload,1-(c.embedding <=> CAST(:vector AS vector))/2 AS score
                FROM knowledge_chunks c WHERE {condition}
                ORDER BY c.embedding <=> CAST(:vector AS vector) LIMIT :count'''), params).mappings().all()
            tokens = list(dict.fromkeys(keyword_tokens(query)))
            keyword = []
            if tokens:
                params['query'] = ' | '.join(tokens)
                # A/B/D 对应问题/标题/正文，权重比 3:2:1，避免 PostgreSQL 默认的 10:4:1 放大通用问法。
                # normalization=32 将 ts_rank_cd 映射为 rank/(rank+1)，不能当作 BM25 或置信度。
                keyword = conn.execute(text(f'''SELECT c.id,c.payload,ts_rank_cd(ARRAY[0.1,0.1,0.2,0.3]::real[],c.keywords,to_tsquery('simple',:query),32) AS score
                    FROM knowledge_chunks c WHERE {condition} AND c.keywords @@ to_tsquery('simple',:query)
                    ORDER BY score DESC,c.id LIMIT :count'''), params).mappings().all()
        # 保留上层召回合并所需的候选结构，不影响前端和 Agent 的响应字段。
        return tuple([{'_id': r['id'], '_source': r['payload'], '_score': r['score']} for r in rows] for rows in (semantic, keyword))


def fuse(semantic, keyword, vector_weight=0.8, keyword_weight=0.2):
    """两路均为 0～1 分数，默认按 0.8/0.2 融合；分数不是答案正确率。"""
    candidates = {}
    for branch, hits in [('vector_score', semantic), ('keyword_score', keyword)]:
        for hit in hits:
            row = candidates.setdefault(hit['_id'], {**hit['_source'], 'vector_score': 0.0, 'keyword_score': 0.0})
            score = max(0.0, float(hit.get('_score') or 0))
            if math.isfinite(score):
                row[branch] = min(1.0, score)
    for row in candidates.values():
        row['score'] = vector_weight * row['vector_score'] + keyword_weight * row['keyword_score']
    return sorted(candidates.values(), key=lambda row: (-row['score'], row['id']))
