"""SCRUM-259: jejak waktu per percobaan dan jawaban mentah markdown, dengan store asli."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.modules.runs import dependencies
from app.modules.runs.execution_store import TableSessionStore
from app.modules.runs.models import Attempt, AttemptStatus
from tests.modules.conftest import as_role, buat_pengguna
from tests.modules.runs.test_execution_runtime import (
    db_session,
    jenis_peraturan,
    start,
)
from tests.modules.runs.test_start_endpoint import siap

__all__ = ["as_role", "buat_pengguna", "db_session", "jenis_peraturan", "siap"]


def _utc(value: datetime) -> datetime:
    """SQLite mengembalikan datetime tanpa zona; PostgreSQL dengan zona."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _attempt(db, store: TableSessionStore, case_index=0, repeat_index=0) -> Attempt:
    db.expire_all()
    return db.scalars(
        select(Attempt).where(
            Attempt.session_id == store.session_id,
            Attempt.snapshot_item_id == store.items[case_index].id,
            Attempt.repetition_no == repeat_index + 1,
        )
    ).one()


def _claimed_store(siap):
    session_id = start(siap)
    dependencies.build_session_queue(siap.db).take_next()
    return TableSessionStore(siap.db, session_id)


def test_claim_stamps_started_at_and_leaves_finished_at_empty(siap):
    # Arrange
    store = _claimed_store(siap)
    before = datetime.now(UTC)

    # Act
    attempt_id = store.prepare_attempt(0, 0)

    # Assert
    after = datetime.now(UTC)
    attempt = _attempt(siap.db, store)
    assert attempt.id == attempt_id
    assert attempt.status == AttemptStatus.RUNNING
    assert attempt.started_at is not None
    assert before <= _utc(attempt.started_at) <= after
    assert attempt.finished_at is None


def test_unclaimed_attempts_keep_empty_timestamps(siap):
    # Arrange
    store = _claimed_store(siap)

    # Act
    store.prepare_attempt(0, 0)

    # Assert
    untouched = _attempt(siap.db, store, repeat_index=1)
    assert untouched.status == AttemptStatus.PENDING
    assert (untouched.started_at, untouched.finished_at) == (None, None)


def test_claim_skips_finished_attempt_without_changing_its_timestamps(siap):
    # Arrange
    store = _claimed_store(siap)
    stamp = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    attempt = _attempt(siap.db, store)
    attempt.status = AttemptStatus.RUNNING
    siap.db.flush()
    attempt.status = AttemptStatus.SUCCEEDED
    attempt.answer_text = "sudah ada"
    attempt.started_at, attempt.finished_at = stamp, stamp
    siap.db.commit()

    # Act
    claimed = store.prepare_attempt(0, 0)

    # Assert
    kept = _attempt(siap.db, store)
    assert claimed is None
    assert (_utc(kept.started_at), _utc(kept.finished_at)) == (stamp, stamp)
