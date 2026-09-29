# 集中读取进程环境变量和工作目录中的 .env。环境变量优先于文件，便于 Docker 覆盖地址。
# 配置在进程内缓存；修改 .env 后应重建或重启对应服务，不会自动热更新。

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# 声明配置类型、默认值及维度边界。SQLite 仅为后备地址，Compose 会配置 MySQL。
class Settings(BaseSettings):
    # 忽略 .env 中 MySQL/Compose 等不属于 Settings 的键，允许服务共用一份环境文件。
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./data/rag.db'
    admin_api_key: str = ''
    es_url: str = 'http://127.0.0.1:9200'
    es_index: str = 'team-rag-bge-zh-v1'
    embedding_base_url: str = 'https://api.siliconflow.cn/v1'
    embedding_model: str = 'BAAI/bge-large-zh-v1.5'
    embedding_api_key: str = ''
    # 模型输出维度必须与 ES 映射完全相同；更换模型后应更换索引并重新向量化。
    embedding_dimensions: int = Field(default=1024, ge=1, le=4096)
    embedding_query_prefix: str = '为这个句子生成表示以用于检索相关文章：'
    # API 与 Worker 共享原文件目录；Compose 挂载持久化卷。
    upload_dir: str = 'data/uploads'
    max_upload_bytes: int = 10 * 1024 * 1024
    max_document_chars: int = 1_000_000
    max_document_chunks: int = 1000
    worker_poll_seconds: float = 2
    # 租约用于进程崩溃后的任务接管，并非模型请求超时；HTTP 请求另有 45 秒超时。
    task_lease_seconds: int = 900
    task_max_attempts: int = 5


# 返回进程内唯一的配置实例，避免每次请求重复读取 .env。测试需要换配置时应显式清理缓存或注入实例。
@lru_cache
def get_settings():
    return Settings()
