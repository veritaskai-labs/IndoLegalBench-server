"""Pemulihan sesi dan worker liveness di PostgreSQL. PBI-12 AC8, SCRUM-217.

Memverifikasi di database PostgreSQL nyata bahwa:
1. Sesi berstatus RUNNING saat restart dipindahkan ke PAUSED dengan stop_reason = RESTART
   dan stopped_at terisi, serta lolos trigger measurement_sessions_guard_update dan
   check constraint ck_measurement_sessions_stop_complete.
2. Attempt RUNNING di-reset ke PENDING dan lolos trigger attempts_protect_answer.
3. check_worker_liveness memindahkan sesi yang stale ke PAUSED.

Dilewati bila TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    MeasurementSession,
    SessionStatus,
    StopReason,
)
from app.modules.runs.recovery import check_worker_liveness, recover_after_restart
from tests.measurement_pg import buat_admin

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
WAKTU_MULAI = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
WAKTU_RESTART = datetime(2026, 11, 5, 8, 15, tzinfo=UTC)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def engine():
    mesin = create_engine(POSTGRES_URL, connect_args={"options": "-c lock_timeout=5000"})
    yield mesin
    mesin.dispose()


@pytest.fixture
def pg_session(engine):
    """Sesi PostgreSQL yang di-rollback setelah tiap tes agar data tidak tertinggal."""
    with engine.connect() as conn:
        trans = conn.begin()
        session = Session(bind=conn)
        try:
            yield session
        finally:
            session.close()
            trans.rollback()


def _buat_sesi_pg(session: Session, product_id: uuid.UUID, admin_id: uuid.UUID) -> uuid.UUID:
    session_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO measurement_sessions (
                id, label, purpose, session_type, product_id, repetitions,
                split_filter, status, created_by, created_at, started_at, updated_at
            ) VALUES (
                :id, 'Sesi PG Recovery', 'trial', 'automatic', :product_id, 2,
                'both', 'running', :admin_id, :now, :now, :now
            )
            """
        ),
        {"id": session_id, "product_id": product_id, "admin_id": admin_id, "now": WAKTU_MULAI},
    )
    return session_id


def _buat_snapshot_dan_attempt_pg(
    session: Session, session_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    snapshot_id = uuid.uuid4()
    session.execute(
        text("INSERT INTO case_snapshots (id, session_id, created_at) VALUES (:id, :sid, :now)"),
        {"id": snapshot_id, "sid": session_id, "now": WAKTU_MULAI},
    )
    case_id = uuid.uuid4()
    version_id = uuid.uuid4()
    # Kasus sintetis dummy di cases & case_versions untuk FK
    session.execute(
        text(
            "INSERT INTO cases (id, case_code, created_by, status) VALUES (:id, 'K-001', (SELECT id FROM users LIMIT 1), 'approved')"
        ),
        {"id": case_id},
    )
    session.execute(
        text(
            """
            INSERT INTO case_versions (id, case_id, version_no, created_by, split_tag, content)
            VALUES (:id, :cid, 1, (SELECT id FROM users LIMIT 1), 'dev', '{"title": "Uji"}'::jsonb)
            """
        ),
        {"id": version_id, "cid": case_id},
    )
    item_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO case_snapshot_items (id, snapshot_id, case_id, case_version_id, body)
            VALUES (:id, :sid, :cid, :vid, '{"split_tag": "dev"}'::jsonb)
            """
        ),
        {"id": item_id, "sid": snapshot_id, "cid": case_id, "vid": version_id},
    )
    attempt_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source, status, created_at, updated_at)
            VALUES (:id, :sid, :item_id, 1, 'automatic', 'running', :now, :now)
            """
        ),
        {"id": attempt_id, "sid": session_id, "item_id": item_id, "now": WAKTU_MULAI},
    )
    return item_id, attempt_id


def test_recover_after_restart_on_postgresql(pg_session):
    """Positif: di PostgreSQL, recovery restart berhasil melewati seluruh trigger dan check constraint."""
    # Arrange
    admin_id = buat_admin(pg_session.connection())
    # Buat produk otomatis (karena sesi berjenis automatic)
    product_id = uuid.uuid4()
    pg_session.execute(
        text(
            """
            INSERT INTO ai_products (
                id, name, created_by, measurement_type, provider_type, base_url,
                model_name, credential_encrypted, credential_hint, rate_limit_per_minute,
                monthly_budget_idr
            ) VALUES (
                :id, :name, :admin_id, 'automatic', 'openai_compatible', 'https://api.test',
                'gpt-test', 'secret'::bytea, 'hint', 60, 500000
            )
            """
        ),
        {"id": product_id, "name": f"Produk Auto {product_id.hex[:6]}", "admin_id": admin_id},
    )
    session_id = _buat_sesi_pg(pg_session, product_id, admin_id)
    _, attempt_id = _buat_snapshot_dan_attempt_pg(pg_session, session_id)

    # Act
    recovered = recover_after_restart(pg_session, at=WAKTU_RESTART)

    # Assert
    assert recovered == [session_id]
    sesi = pg_session.get(MeasurementSession, session_id)
    assert sesi.status == SessionStatus.PAUSED
    assert sesi.stop_reason == StopReason.RESTART
    assert sesi.stopped_at == WAKTU_RESTART

    # Attempt running di-reset ke pending
    att = pg_session.get(Attempt, attempt_id)
    assert att.status == AttemptStatus.PENDING


def test_check_worker_liveness_on_postgresql(pg_session):
    """Positif: worker liveness timeout di PostgreSQL memindahkan sesi stale ke PAUSED."""
    # Arrange
    admin_id = buat_admin(pg_session.connection())
    product_id = uuid.uuid4()
    pg_session.execute(
        text(
            """
            INSERT INTO ai_products (
                id, name, created_by, measurement_type, provider_type, base_url,
                model_name, credential_encrypted, credential_hint, rate_limit_per_minute,
                monthly_budget_idr
            ) VALUES (
                :id, :name, :admin_id, 'automatic', 'openai_compatible', 'https://api.test',
                'gpt-test', 'secret'::bytea, 'hint', 60, 500000
            )
            """
        ),
        {"id": product_id, "name": f"Produk Auto {product_id.hex[:6]}", "admin_id": admin_id},
    )
    session_id = _buat_sesi_pg(pg_session, product_id, admin_id)
    _, attempt_id = _buat_snapshot_dan_attempt_pg(pg_session, session_id)

    sekarang = WAKTU_MULAI + timedelta(minutes=10)

    # Act
    stale_ids = check_worker_liveness(pg_session, at=sekarang)

    # Assert
    assert stale_ids == [session_id]
    sesi = pg_session.get(MeasurementSession, session_id)
    assert sesi.status == SessionStatus.PAUSED
    assert sesi.stop_reason == StopReason.RESTART
    assert pg_session.get(Attempt, attempt_id).status == AttemptStatus.PENDING
