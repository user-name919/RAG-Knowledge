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


@pytest.fixture
def factory():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(factory):
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    # No lifespan: these tests use isolated SQLite and mocked external services.
    yield TestClient(app, headers={'api-key': os.environ['ADMIN_API_KEY']})
    app.dependency_overrides.clear()
