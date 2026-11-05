"""Competing resumes: real unique-index wait/rollback and row refresh, not mocks."""

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.runs.models import MeasurementSession, SessionProductCopy, SessionStatus
from app.modules.runs.repository import SessionStatusRepository
from app.modules.runs.session_control import resume_session
from app.modules.runs.session_control_repository import SessionControlRepository
from app.shared.exceptions import ConflictError
from app.shared.security import CurrentUser, Role
from tests.integration.test_session_status_change_pg import engine
from tests.measurement_pg import buat_admin, buat_sesi
from tests.modules.providers.test_product_lists import _otomatis

__all__ = ["engine"]
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL required"),
]
NOW = datetime(2026, 11, 5, 10, tzinfo=UTC)


def candidate(engine):
    with Session(engine) as db:
        admin = buat_admin(db.connection())
        product = _otomatis(f"Resume race {uuid.uuid4().hex}", created_by=admin)
        db.add(product)
        db.flush()
        session_id = buat_sesi(db.connection(), product.id, admin, session_type="automatic")
        db.add(
            SessionProductCopy(
                session_id=session_id,
                product_id=product.id,
                name=product.name,
                measurement_type="automatic",
                provider_type="openai_compatible",
                base_url=product.base_url,
                model_name=product.model_name,
            )
        )
        db.commit()
        statuses = SessionStatusRepository(db)
        statuses.change_status(session_id, SessionStatus.RUNNING, at=NOW)
        statuses.change_status(session_id, SessionStatus.PAUSED, at=NOW, stop_reason="user")
        return session_id, admin


@pytest.mark.parametrize("same_session", [False, True])
def test_two_resumes_have_one_winner_with_atomic_loser(engine, monkeypatch, same_session):
    first = candidate(engine)
    second = first if same_session else candidate(engine)
    barrier = threading.Barrier(2)
    if not same_session:
        original = SessionControlRepository.another_running

        def see_empty_slot(self, session_id):
            result = original(self, session_id)
            assert not result
            barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(SessionControlRepository, "another_running", see_empty_slot)
    outcomes = []

    def resume(item):
        session_id, admin = item
        try:
            with Session(engine) as db:
                if same_session:
                    barrier.wait(timeout=10)
                response = resume_session(
                    db,
                    session_id,
                    actor=CurrentUser(admin, "Admin", "admin@test.invalid", Role.ADMIN),
                    now=NOW,
                    long_stop_days=7,
                )
                outcomes.append((session_id, response))
        except Exception as error:
            outcomes.append((session_id, error))

    threads = [threading.Thread(target=resume, args=(item,)) for item in (first, second)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
            assert not thread.is_alive()
        assert len(outcomes) == 2
        failures = [(sid, value) for sid, value in outcomes if isinstance(value, Exception)]
        assert len(failures) == 1
        loser, error = failures[0]
        assert isinstance(error, ConflictError)
        assert error.code == (
            "SESSION_NOT_RESUMABLE" if same_session else "ANOTHER_SESSION_RUNNING"
        )
        with Session(engine) as db:
            if not same_session:
                row = db.get(MeasurementSession, loser)
                assert row.status == "paused" and row.stopped_at == NOW
                assert not row.model_version_unverified and not row.resume_version_check_pending
    finally:
        # Only synthetic rows in this disposable database; leave no active slot for other tests.
        with engine.begin() as connection:
            connection.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id.in_([first[0], second[0]]))
                .values(
                    status=SessionStatus.FINISHED,
                    model_version_unverified=False,
                    resumed_after_long_pause=False,
                    resume_version_check_pending=False,
                )
            )
