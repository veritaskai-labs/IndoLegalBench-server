"""AC7: lock contention must refresh stale ORM state before control decisions."""

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.runs.models import MeasurementSession, SessionStatus
from app.modules.runs.repository import SessionStatusRepository
from app.modules.runs.session_control import stop_session
from app.shared.exceptions import ConflictError
from tests.integration.test_session_status_change_pg import _tunggu_kunci, engine
from tests.measurement_pg import buat_admin, buat_sesi
from tests.modules.providers.test_product_lists import _otomatis

__all__ = ["engine"]
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL required"),
]
NOW = datetime(2026, 11, 5, 9, tzinfo=UTC)


@pytest.mark.parametrize("target", [SessionStatus.PAUSED, SessionStatus.CANCELLED])
@pytest.mark.parametrize("winner", ["finish", "archive", "accept"])
def test_control_waits_then_rechecks_committed_state(engine, target, winner):
    """Negative/race: finish/archive/accept wins; control must not overwrite it."""
    with Session(engine) as setup:
        admin = buat_admin(setup.connection())
        product = _otomatis(f"Control race {uuid.uuid4().hex}", created_by=admin)
        setup.add(product)
        setup.flush()
        session_id = buat_sesi(setup.connection(), product.id, admin, session_type="automatic")
        setup.commit()
        SessionStatusRepository(setup).change_status(session_id, SessionStatus.RUNNING, at=NOW)

    loaded, proceed, pid_ready = threading.Event(), threading.Event(), threading.Event()
    outcome = {}

    def control():
        try:
            with Session(engine, expire_on_commit=False) as db:
                # Populate the identity map with stale running state intentionally.
                row = db.get(MeasurementSession, session_id)
                assert row.status == SessionStatus.RUNNING
                db.commit()
                loaded.set()
                assert proceed.wait(10)
                outcome["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
                db.commit()
                pid_ready.set()
                stop_session(db, session_id, target=target, now=NOW)
        except Exception as error:
            outcome["error"] = error

    thread = threading.Thread(target=control)
    thread.start()
    assert loaded.wait(10)
    with engine.begin() as first:
        first.execute(
            text("SELECT 1 FROM measurement_sessions WHERE id=:id FOR UPDATE"),
            {"id": session_id},
        )
        proceed.set()
        assert pid_ready.wait(10)
        _tunggu_kunci(engine, outcome["pid"])
        if winner == "finish":
            values = "status='finished', finished_at=now()"
        elif winner == "archive":
            values = "status='finished', archived_at=now(), archived_by=created_by"
        else:
            values = (
                "status='finished_with_failures', accepted_as_is_at=now(), "
                "accepted_as_is_by=created_by, accepted_as_is_reason='accepted by Admin'"
            )
        first.execute(
            text(f"UPDATE measurement_sessions SET {values} WHERE id=:id"), {"id": session_id}
        )
    thread.join(10)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), ConflictError)
    assert (
        outcome["error"].code
        == {
            "finish": "INVALID_TRANSITION",
            "archive": "SESSION_ARCHIVED",
            "accept": "SESSION_ACCEPTED",
        }[winner]
    )
    with Session(engine) as check:
        row = check.get(MeasurementSession, session_id)
        assert row.status == (
            SessionStatus.FINISHED_WITH_FAILURES if winner == "accept" else SessionStatus.FINISHED
        )
        assert row.stopped_at is None
        assert row.stop_reason is None
