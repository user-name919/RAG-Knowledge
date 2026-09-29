# Elasticsearch 适配层与双路评分融合。ES 同时保存全文索引和稠密向量。
# 仅负责候选检索；召回结果的最终权限和版本校验由 main.valid_candidates 执行。

import json
import math
import httpx
from .config import get_settings


# 检索存储故障的统一脱敏异常，供 API 和后台任务按各自方式处理。
class SearchError(RuntimeError):
    pass


# 围绕 ES HTTP API 提供建索引、写入、清理和候选检索能力。
class SearchStore:
    # 允许传入独立配置，集成测试使用专属 ES 索引，不修改默认业务索引。
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    # 统一处理 ES 请求和错误。trust_env=False 避免本地/容器内 ES 请求误走系统代理。
    # 只向上暴露 HTTP 状态或错误类别，不回传 ES 的完整响应正文。
    def request(self, method, path, **kwargs):
        try:
            with httpx.Client(base_url=self.settings.es_url, timeout=45, trust_env=False) as client:
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise SearchError('Elasticsearch HTTP ' + str(exc.response.status_code)) from None
        except (httpx.RequestError, ValueError):
            raise SearchError('Elasticsearch connection or response error') from None

    # 创建单分片、零副本的开发索引；存在时检查模型、维度和 schema 版本。
    # keyword 字段用于过滤，cjk 文本字段用于词项检索，dense_vector 用于余弦近邻检索。
    def ensure_index(self):
        s = self.settings
        mappings = {
            # 记录模型标识和结构版本；相同维度也不代表不同模型的向量可混合。
            '_meta': {'embedding_model': s.embedding_model, 'schema_version': 1},
            # 拒绝未声明字段，让写入格式错误显式失败，而不是自动扩展出错误映射。
            'dynamic': 'strict',
            'properties': {
                **{k: {'type': 'keyword'} for k in ['id', 'qa_id', 'knowledge_base_id', 'document_id', 'tags', 'embedding_model']},
                # cjk 为中文等文本生成词元；exact 子字段保留整值，但当前检索尚未使用它。
                **{k: {'type': 'text', 'analyzer': 'cjk', 'fields': {'exact': {'type': 'keyword', 'ignore_above': 512}}} for k in ['title', 'question', 'stand_query', 'content']},
                'type': {'type': 'keyword'},
                'heading': {'type': 'text', 'analyzer': 'cjk'},
                'page_number': {'type': 'integer'},
                'version': {'type': 'integer'},
                # 来源字段保留在 _source 用于追溯，不建立搜索索引。
                'source_uri': {'type': 'keyword', 'index': False},
                'source_location': {'type': 'keyword', 'index': False},
                'updated_at': {'type': 'date'},
                'embedding': {'type': 'dense_vector', 'dims': s.embedding_dimensions, 'index': True, 'similarity': 'cosine'},
            },
        }
        try:
            self.request('PUT', '/' + s.es_index, json={'settings': {'number_of_shards': 1, 'number_of_replicas': 0}, 'mappings': mappings})
        except SearchError:
            # A concurrent creator or an existing index is acceptable only if metadata matches.
            existing = self.request('GET', '/' + s.es_index + '/_mapping')[s.es_index]['mappings']
            if existing.get('_meta') != mappings['_meta'] or existing['properties']['embedding']['dims'] != s.embedding_dimensions:
                raise SearchError('Index model/schema mismatch; use a new ES_INDEX and reindex')
            # 兼容旧 QA 索引，仅增补文件来源字段，不重建或删除原有记录。
            additions = {key: mappings['properties'][key] for key in ('type', 'heading', 'page_number') if key not in existing['properties']}
            if additions:
                self.request('PUT', '/' + s.es_index + '/_mapping', json={'properties': additions})

    # 以 NDJSON 批量写入片段，确定性 _id 使同版本重试覆盖已有记录。
    # refresh=wait_for 等待搜索可见后再发布 MySQL 索引版本；HTTP 成功仍需检查批量子项错误。
    def put_chunks(self, chunks, vectors):
        # ES bulk 使用一行操作元数据、一行正文的 NDJSON；最后也必须带换行。
        lines = []
        for chunk, vector in zip(chunks, vectors):
            payload = {k: v for k, v in chunk.items() if k != 'embedding_text'}
            payload['embedding'] = vector
            lines.extend([json.dumps({'index': {'_index': self.settings.es_index, '_id': chunk['id']}}), json.dumps(payload, ensure_ascii=False)])
        response = self.request('POST', '/_bulk?refresh=wait_for', content='\n'.join(lines) + '\n', headers={'Content-Type': 'application/x-ndjson'})
        # bulk 顶层 HTTP 200 不保证每条成功；部分失败交给任务重试，用确定性 ID 覆盖。
        if response.get('errors'):
            raise SearchError('Elasticsearch bulk indexing partially failed; retry uses deterministic IDs')

    # 清理一个 QA 的全部、旧版本或指定版本片段。
    # 更新仅删旧版本，过期任务仅删自己的版本，避免误删其他任务刚写入的新数据。
    def delete_qa(self, qa_id, before_version=None, exact_version=None):
        filters = [{'term': {'qa_id': qa_id}}]
        if before_version is not None:
            filters.append({'range': {'version': {'lt': before_version}}})
        if exact_version is not None:
            filters.append({'term': {'version': exact_version}})
        response = self.request('POST', '/' + self.settings.es_index + '/_delete_by_query?refresh=true&conflicts=proceed', json={'query': {'bool': {'filter': filters}}})
        # 删除冲突不当作彻底成功，否则 MySQL 会错误地认为清理已经完成。
        if response.get('failures') or response.get('version_conflicts'):
            raise SearchError('Elasticsearch cleanup incomplete; retry needed')

    # 文件片段和 QA 可以位于同一个节点；删除文件索引时明确限定 type。
    def delete_document(self, document_id, before_version=None, exact_version=None):
        filters = [{'term': {'document_id': document_id}}, {'term': {'type': 'document'}}]
        if before_version is not None:
            filters.append({'range': {'version': {'lt': before_version}}})
        if exact_version is not None:
            filters.append({'term': {'version': exact_version}})
        response = self.request('POST', '/' + self.settings.es_index + '/_delete_by_query?refresh=true&conflicts=proceed', json={'query': {'bool': {'filter': filters}}})
        if response.get('failures') or response.get('version_conflicts'):
            raise SearchError('Document index cleanup incomplete')

    # 使用同一组过滤条件分别执行关键词和向量检索，返回两份 ES 命中列表。
    # count 是候选规模，不是最终 top_k；返回时排除向量，减少数据传输。
    def search(self, query, vector, kb_ids, document_ids, tags, count):
        filters = [{'terms': {'knowledge_base_id': kb_ids}}]
        if document_ids:
            filters.append({'terms': {'document_id': document_ids}})
        # All requested tags must match.
        filters.extend({'term': {'tags': tag}} for tag in tags)
        source = {'excludes': ['embedding']}
        # 问题和标准问法的词项匹配权重为 3，标题为 2，正文为 1；这不是两路融合权重。
        keyword = self.request('POST', '/' + self.settings.es_index + '/_search', json={
            'size': count, '_source': source,
            'query': {'bool': {'filter': filters, 'must': [{'multi_match': {'query': query, 'fields': ['question^3', 'stand_query^3', 'title^2', 'content']}}]}},
        })['hits']['hits']
        # num_candidates 是近邻搜索候选预算；过滤放在 kNN 内部，避免先取无权限近邻再过滤。
        semantic = self.request('POST', '/' + self.settings.es_index + '/_search', json={
            'size': count, '_source': source,
            'knn': {'field': 'embedding', 'query_vector': vector, 'k': count, 'num_candidates': min(10000, count * 5), 'filter': {'bool': {'filter': filters}}},
        })['hits']['hits']
        return semantic, keyword


# 按片段 ID 合并两路候选并去重，缺失分支按 0 计分。
# 向量使用 ES 余弦分数，BM25 用 s/(s+8) 压缩，再按默认 0.8/0.2 加权。
# 常数 8 是初始校准参数，最终分数不是置信度；同分按 ID 排序以稳定输出。
def fuse(semantic, keyword, vector_weight=0.8, keyword_weight=0.2):
    """Cosine ES score=(1+cos)/2; BM25 is saturated by s/(s+8). Not a probability."""
    candidates = {}
    for branch, hits in [('vector_score', semantic), ('keyword_score', keyword)]:
        for hit in hits:
            # 同一个片段在两路命中时共用一条结果，分别填入分数；未命中的那一路保持 0。
            row = candidates.setdefault(hit['_id'], {**hit['_source'], 'vector_score': 0.0, 'keyword_score': 0.0})
            score = max(0.0, float(hit.get('_score') or 0))
            if not math.isfinite(score):
                continue
            # ES 余弦得分已映射到 0～1；BM25 没有固定上限，因此使用饱和函数压缩。
            row[branch] = min(1.0, score) if branch == 'vector_score' else score / (score + 8.0)
    # 融合只负责排序。阈值、最终 top_k 和权威状态校验由接口层处理。
    for row in candidates.values():
        row['score'] = vector_weight * row['vector_score'] + keyword_weight * row['keyword_score']
    return sorted(candidates.values(), key=lambda row: (-row['score'], row['id']))
