import os
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./plumbus_test.db"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/15"
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-long-enough"
os.environ["TOKEN_ENCRYPTION_KEY"] = "test-token-secret-that-is-long-enough"
os.environ["MOCK_PLEX"] = "true"
os.environ["COOKIE_SECURE"] = "false"
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.database import Base, SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def database_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Path("plumbus_test.db").unlink(missing_ok=True)


@pytest.fixture
def db() -> Session:
    session = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
