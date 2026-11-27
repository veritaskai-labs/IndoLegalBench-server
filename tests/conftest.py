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
# The app lifespan runs session recovery against this URL on every TestClient
# start. Without the pin it is .env's or the default Postgres, so unit tests
# would connect to a real database (tests/test_unit_tests_use_sqlite.py).
# Shared-cache memory, not plain sqlite://: plain in-memory SQLite is one
# database per thread, and TestClient starts the app in a new thread, so the
# lifespan re-created all 47 tables on every start (94 ms, CI Test step
# 374 s -> 695 s). Shared, they are created once per process (13 ms a start).
# Each xdist worker is its own process, so workers still never share it.
os.environ["DATABASE_URL"] = "sqlite:///file:ilb_app_test?mode=memory&cache=shared&uri=true"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.shared.database import Base, get_db
from tests.rbac_probe import probe

if not any(getattr(route, "path", None) == "/_test/rbac/contributor" for route in app.routes):
    app.include_router(probe)

TEST_DATABASE_URL = "sqlite://"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Tabel dengan trigger SQLite yang menolak DELETE (app/modules/audit/models.py).
_APPEND_ONLY_TABLES = frozenset({"audit_logs"})


@pytest.fixture(scope="session")
def _test_schema():
    """Buat semua tabel sekali per proses pytest (sekali per worker xdist).

    Dulu create_all + drop_all 47 tabel jalan di setiap test (~94 ms per test).
    """
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(_test_schema):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        # Isolasi tetap sama: setiap test mulai dari tabel kosong. DELETE dari
        # tabel anak ke induk (~5 ms) jauh lebih murah daripada membuat ulang skema.
        with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                if table.name in _APPEND_ONLY_TABLES:
                    # Trigger SQLite menolak DELETE (AC2 PBI-18). Drop + create
                    # memasang trigger itu lagi lewat event after_create.
                    table.drop(connection)
                    table.create(connection)
                else:
                    connection.execute(table.delete())


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
