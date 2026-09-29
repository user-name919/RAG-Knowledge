# 调用兼容 Embeddings 协议的外部模型，把输入文本转换为向量。
# 此处不生成答案；异常转换成脱敏错误，避免供应商响应中的原文或凭据进入日志。

import math
import httpx
from .config import get_settings
from .tokenization import token_count


# 外部模型故障的统一异常类型；API 转成 503，Worker 据此记录脱敏错误并重试。
class EmbeddingError(RuntimeError):
    pass


# 封装模型配置和同步 HTTP 调用；可注入 Settings 以便隔离测试。
class EmbeddingClient:
    # 未显式传入配置时读取进程级 Settings，不在客户端内部保存第二份凭据配置。
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    # 输入文本列表，按原顺序返回向量列表。query=True 时仅给查询添加检索前缀。
    # 每批最多 16 条，检查索引、维度、有限数值与零向量；任一批失败则整次调用抛错，不返回半份结果。
    def embed(self, texts, query=False):
        settings = self.settings
        if not settings.embedding_api_key:
            raise EmbeddingError('EMBEDDING_API_KEY is not configured')
        # 文档输入保持原样，仅查询使用 BGE 的检索指令，不能给两端都盲目追加同一前缀。
        inputs = [settings.embedding_query_prefix + t if query else t for t in texts]
        # 使用与当前 BGE 模型匹配的 tokenizer，包含特殊 token 后不超过 512。
        if any(not t or token_count(t) > 512 for t in inputs):
            raise EmbeddingError('Embedding input exceeds 512 tokens; shorten the query or re-chunk the content')
        vectors = []
        # 限制请求时长且禁止自动重定向，避免鉴权请求被意外发往其他地址。
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
                    # 供应商返回顺序未必与请求相同，必须依据 index 恢复文本和向量的一一对应。
                    data = sorted(response.json()['data'], key=lambda item: item['index'])
                    # 同时检查数量、重复索引与缺失索引，不能只检查返回列表长度。
                    if [item['index'] for item in data] != list(range(len(batch))):
                        raise ValueError('Invalid embedding indices')
                    for item in data:
                        vector = item['embedding']
                        if len(vector) != settings.embedding_dimensions or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                            raise ValueError('Invalid embedding dimensions or values')
                        # 余弦相似度不能对零向量计算；提前拒绝，避免向 检索片段 写入不可用数据。
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
