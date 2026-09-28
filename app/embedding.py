import math
import httpx
from .config import get_settings


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def embed(self, texts, query=False):
        settings = self.settings
        if not settings.embedding_api_key:
            raise EmbeddingError('EMBEDDING_API_KEY is not configured')
        inputs = [settings.embedding_query_prefix + t if query else t for t in texts]
        # Conservative UTF-8 byte bound: avoids exceeding this BGE model's 512-token window.
        if any(not t or len(t.encode('utf-8')) > 480 for t in inputs):
            raise EmbeddingError('Embedding input exceeds 480 UTF-8 bytes; shorten the query or re-chunk the content')
        vectors = []
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            for start in range(0, len(inputs), 16):
                batch = inputs[start:start + 16]
                try:
                    response = client.post(
                        settings.embedding_base_url.rstrip('/') + '/embeddings',
                        headers={'Authorization': 'Bearer ' + settings.embedding_api_key},
                        json={'model': settings.embedding_model, 'input': batch, 'encoding_format': 'float'},
                    )
                    response.raise_for_status()
                    data = sorted(response.json()['data'], key=lambda item: item['index'])
                    if [item['index'] for item in data] != list(range(len(batch))):
                        raise ValueError('Invalid embedding indices')
                    for item in data:
                        vector = item['embedding']
                        if len(vector) != settings.embedding_dimensions or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                            raise ValueError('Invalid embedding dimensions or values')
                        if sum(v*v for v in vector) == 0:
                            raise ValueError('Zero embedding vector')
                        vectors.append(vector)
                except httpx.HTTPStatusError as exc:
                    # Do not persist response bodies: they may contain input data or credentials.
                    raise EmbeddingError('Embedding provider HTTP ' + str(exc.response.status_code)) from None
                except httpx.RequestError:
                    raise EmbeddingError('Embedding provider connection/timeout error') from None
                except (KeyError, TypeError, ValueError):
                    raise EmbeddingError('Embedding provider returned invalid data') from None
        return vectors
