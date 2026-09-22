"""Setup bersama untuk seluruh test.

Test memakai SQLite in-memory supaya CI tidak butuh PostgreSQL yang
menyala. Kalau nanti ada test yang butuh fitur khusus PostgreSQL
(misalnya JSONB), pisahkan ke test integrasi tersendiri.
"""

import os

# Pin before app.main → get_settings() so a local .env AUTH_OIDC_MODE=zitadel
# cannot send pytest at Cloud.
os.environ["AUTH_OIDC_MODE"] = "fake"
os.environ["APP_ENV"] = "local"
os.environ["AUTH_DONE_URL_OVERRIDE"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.shared.database import Base, get_db

TEST_DATABASE_URL = "sqlite://"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
