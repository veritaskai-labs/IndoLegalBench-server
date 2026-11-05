"""SCRUM-249: SA-requested trigger cases, rolled-back data and DDL on PostgreSQL."""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, insert, select, update

from app.modules.runs.models import Attempt, MeasurementSession, SessionProductCopy
from tests.measurement_pg import (
    INSUFFICIENT_PRIVILEGE,
    buat_admin,
    buat_item_snapshot,
    buat_produk,
    buat_sesi,
    galat,
    ubah_status,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def _session(conn, *, observed="qa-observed"):
    admin = buat_admin(conn)
    product = buat_produk(conn, admin)
    session = buat_sesi(conn, product, admin)
    item = buat_item_snapshot(conn, session, admin)
    conn.execute(
        insert(SessionProductCopy).values(
            session_id=session, product_id=product, name="QA copy", measurement_type="manual"
        )
    )
    conn.execute(
        insert(Attempt).values(
            session_id=session,
            snapshot_item_id=item,
            repetition_no=1,
            source="manual",
            status="succeeded",
            answer_text="QA answer",
            model_version=observed,
        )
    )
    ubah_status(conn, session, "running")
    return session


def _initialize(conn, session):
    return conn.execute(
        update(SessionProductCopy)
        .where(SessionProductCopy.session_id == session)
        .values(model_version_at_start="qa-observed")
    )


def test_evidence_from_another_session_is_refused(conn):
    target = _session(conn, observed=None)
    other = _session(conn)
    assert galat(conn, lambda: _initialize(conn, target)).sqlstate == INSUFFICIENT_PRIVILEGE
    assert _initialize(conn, other).rowcount == 1  # Same-session positive control.


def test_finished_session_cannot_initialize_even_with_matching_evidence(conn):
    session = _session(conn)
    ubah_status(conn, session, "finished")
    assert galat(conn, lambda: _initialize(conn, session)).sqlstate == INSUFFICIENT_PRIVILEGE
    assert (
        conn.scalar(select(MeasurementSession.status).where(MeasurementSession.id == session))
        == "finished"
    )


def test_downgrade_restores_old_trigger_and_preserves_data(conn):
    session = _session(conn)
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/d49a1f2b3c60_initialize_worker_model_version.py"
    )
    spec = importlib.util.spec_from_file_location("worker_version_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(conn)):
        migration.downgrade()
    assert galat(conn, lambda: _initialize(conn, session)).sqlstate == INSUFFICIENT_PRIVILEGE
    assert (
        conn.scalar(select(Attempt.answer_text).where(Attempt.session_id == session)) == "QA answer"
    )
    with Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
    assert _initialize(conn, session).rowcount == 1
