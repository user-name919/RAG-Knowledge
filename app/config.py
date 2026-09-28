from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./data/rag.db'
    admin_api_key: str = ''
    es_url: str = 'http://127.0.0.1:9200'
    es_index: str = 'team-rag-bge-zh-v1'
    embedding_base_url: str = 'https://api.siliconflow.cn/v1'
    embedding_model: str = 'BAAI/bge-large-zh-v1.5'
    embedding_api_key: str = ''
    embedding_dimensions: int = Field(default=1024, ge=1, le=4096)
    embedding_query_prefix: str = '为这个句子生成表示以用于检索相关文章：'
    worker_poll_seconds: float = 2
    task_lease_seconds: int = 900
    task_max_attempts: int = 5


@lru_cache
def get_settings():
    return Settings()
