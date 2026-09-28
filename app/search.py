import json
import math
import httpx
from .config import get_settings


class SearchError(RuntimeError):
    pass


class SearchStore:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

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

    def ensure_index(self):
        s = self.settings
        mappings = {
            '_meta': {'embedding_model': s.embedding_model, 'schema_version': 1},
            'dynamic': 'strict',
            'properties': {
                **{k: {'type': 'keyword'} for k in ['id', 'qa_id', 'knowledge_base_id', 'document_id', 'tags', 'embedding_model']},
                **{k: {'type': 'text', 'analyzer': 'cjk', 'fields': {'exact': {'type': 'keyword', 'ignore_above': 512}}} for k in ['title', 'question', 'stand_query', 'content']},
                'version': {'type': 'integer'},
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

    def put_chunks(self, chunks, vectors):
        lines = []
        for chunk, vector in zip(chunks, vectors):
            payload = {k: v for k, v in chunk.items() if k != 'embedding_text'}
            payload['embedding'] = vector
            lines.extend([json.dumps({'index': {'_index': self.settings.es_index, '_id': chunk['id']}}), json.dumps(payload, ensure_ascii=False)])
        response = self.request('POST', '/_bulk?refresh=wait_for', content='\n'.join(lines) + '\n', headers={'Content-Type': 'application/x-ndjson'})
        if response.get('errors'):
            raise SearchError('Elasticsearch bulk indexing partially failed; retry uses deterministic IDs')

    def delete_qa(self, qa_id, before_version=None, exact_version=None):
        filters = [{'term': {'qa_id': qa_id}}]
        if before_version is not None:
            filters.append({'range': {'version': {'lt': before_version}}})
        if exact_version is not None:
            filters.append({'term': {'version': exact_version}})
        response = self.request('POST', '/' + self.settings.es_index + '/_delete_by_query?refresh=true&conflicts=proceed', json={'query': {'bool': {'filter': filters}}})
        if response.get('failures') or response.get('version_conflicts'):
            raise SearchError('Elasticsearch cleanup incomplete; retry needed')

    def search(self, query, vector, kb_ids, document_ids, tags, count):
        filters = [{'terms': {'knowledge_base_id': kb_ids}}]
        if document_ids:
            filters.append({'terms': {'document_id': document_ids}})
        # All requested tags must match.
        filters.extend({'term': {'tags': tag}} for tag in tags)
        source = {'excludes': ['embedding']}
        keyword = self.request('POST', '/' + self.settings.es_index + '/_search', json={
            'size': count, '_source': source,
            'query': {'bool': {'filter': filters, 'must': [{'multi_match': {'query': query, 'fields': ['question^3', 'stand_query^3', 'title^2', 'content']}}]}},
        })['hits']['hits']
        semantic = self.request('POST', '/' + self.settings.es_index + '/_search', json={
            'size': count, '_source': source,
            'knn': {'field': 'embedding', 'query_vector': vector, 'k': count, 'num_candidates': min(10000, count * 5), 'filter': {'bool': {'filter': filters}}},
        })['hits']['hits']
        return semantic, keyword


def fuse(semantic, keyword, vector_weight=0.8, keyword_weight=0.2):
    """Cosine ES score=(1+cos)/2; BM25 is saturated by s/(s+8). Not a probability."""
    candidates = {}
    for branch, hits in [('vector_score', semantic), ('keyword_score', keyword)]:
        for hit in hits:
            row = candidates.setdefault(hit['_id'], {**hit['_source'], 'vector_score': 0.0, 'keyword_score': 0.0})
            score = max(0.0, float(hit.get('_score') or 0))
            if not math.isfinite(score):
                continue
            row[branch] = min(1.0, score) if branch == 'vector_score' else score / (score + 8.0)
    for row in candidates.values():
        row['score'] = vector_weight * row['vector_score'] + keyword_weight * row['keyword_score']
    return sorted(candidates.values(), key=lambda row: (-row['score'], row['id']))
