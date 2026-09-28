# 单元测试公共夹具。导入应用前切换到测试配置，避免访问开发数据库。
# 测试通过依赖覆盖注入独立 SQLite 会话；MySQL 行锁语义另由集成脚本验证。

import os
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['ADMIN_API_KEY'] = 'test-admin-key-not-for-deployment-123'

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_db
from app.main import app
from app.models import Base


# 每个用例创建独立内存库；StaticPool 让 TestClient 的不同线程使用同一个数据库连接。
# 测试结束释放引擎，避免不同测试共享状态。
@pytest.fixture
def factory():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


# 覆盖应用的会话依赖并使用测试管理员 Key；不用 lifespan，避免启动真实 ES。
# 用例结束清除覆盖，保证后续测试不会继续使用已销毁的会话。
@pytest.fixture
def client(factory):
    # 向当前测试请求提供独立会话，退出时回滚未提交内容并关闭会话。
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    # No lifespan: these tests use isolated SQLite and mocked external services.
    yield TestClient(app, headers={'api-key': os.environ['ADMIN_API_KEY']})
    app.dependency_overrides.clear()
