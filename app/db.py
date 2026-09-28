# 共享的 SQLAlchemy 引擎及会话工厂。API 每个请求独立使用会话，Worker 每段事务单独建会话。
# 本模块只管理连接生命周期，提交事务的时机由具体业务函数决定。

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import get_settings

# 连接出池前检查可用性，降低数据库重启后取到失效连接的概率。
engine = create_engine(get_settings().database_url, pool_pre_ping=True)
# commit 后保留对象字段，便于关闭会话前构造响应；需要最新状态时仍应重新查询。
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# FastAPI 的会话依赖。yield 后关闭会话；未提交的事务会回滚。
# 此处不会自动 commit，因此接口异常不会把批量写入中的部分记录提交。
def get_db():
    with SessionLocal() as session:
        yield session
