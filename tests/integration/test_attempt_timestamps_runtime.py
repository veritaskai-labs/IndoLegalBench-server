"""SCRUM-259: jejak waktu per percobaan dan jawaban mentah markdown, dengan store asli."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.modules.runs import dependencies
from app.modules.runs.execution_store import TableSessionStore
from app.modules.runs.models import Attempt, AttemptStatus, CallLog
from app.modules.runs.ports import AttemptRecord
from app.shared.security import Role
from tests.modules.conftest import as_role, buat_pengguna
from tests.modules.runs.test_execution_runtime import (
    Provider,
    db_session,
    jenis_peraturan,
    runner,
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


@pytest.mark.parametrize(
    "behavior,expected",
    [
        ("ok", {AttemptStatus.SUCCEEDED}),
        ("error", {AttemptStatus.TECHNICAL_FAILURE}),
        ("mixed", {AttemptStatus.SUCCEEDED, AttemptStatus.TECHNICAL_FAILURE}),
    ],
)
def test_every_finished_attempt_has_ordered_timestamps_for_success_and_failure(
    siap, behavior, expected
):
    # Arrange
    session_id = start(siap)
    run = runner(siap, Provider(behavior))
    before = datetime.now(UTC)

    # Act
    run()

    # Assert
    after = datetime.now(UTC)
    siap.db.expire_all()
    attempts = siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all()
    assert len(attempts) == 9
    assert {attempt.status for attempt in attempts} == expected
    for attempt in attempts:
        assert attempt.started_at is not None
        assert attempt.finished_at is not None
        assert before <= _utc(attempt.started_at) <= _utc(attempt.finished_at) <= after


def test_finished_at_is_the_instant_the_response_arrived_in_the_call_log(siap):
    # Arrange
    session_id = start(siap)
    run = runner(siap, Provider("mixed"))

    # Act
    run()

    # Assert
    siap.db.expire_all()
    attempts = siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all()
    logs = {log.attempt_id: log for log in siap.db.scalars(select(CallLog))}
    assert len(logs) == 9
    for attempt in attempts:
        assert attempt.finished_at is not None
        assert _utc(attempt.finished_at) == _utc(logs[attempt.id].ended_at)
        assert _utc(attempt.started_at) <= _utc(logs[attempt.id].started_at)


def test_save_without_reported_end_time_still_stamps_finished_at(siap):
    # Arrange
    store = _claimed_store(siap)
    attempt_id = store.prepare_attempt(0, 0)
    record = AttemptRecord(
        session_id=store.session_id,
        attempt_id=attempt_id,
        case_index=0,
        repeat_index=0,
        prompt="p",
        text="jawaban",
        model_version="model-v1",
        latency_ms=5,
        raw={},
        error=None,
        failure_kind=None,
    )
    before = datetime.now(UTC)

    # Act
    store.save_attempt(record)
    siap.db.commit()

    # Assert
    attempt = _attempt(siap.db, store)
    assert attempt.status == AttemptStatus.SUCCEEDED
    assert attempt.finished_at is not None
    assert before <= _utc(attempt.finished_at) <= datetime.now(UTC)


def _stamps(attempts) -> dict:
    return {
        attempt.id: (_utc(attempt.started_at), _utc(attempt.finished_at)) for attempt in attempts
    }


def _failed_run(siap):
    """Sesi mixed selesai: satu percobaan gagal, delapan berhasil."""
    session_id = start(siap)
    run = runner(siap, Provider("mixed"))
    run()
    siap.db.expire_all()
    attempts = siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all()
    failed = [a for a in attempts if a.status == AttemptStatus.TECHNICAL_FAILURE]
    assert len(failed) == 1
    return session_id, run, failed[0], [a for a in attempts if a.id != failed[0].id]


def test_retry_clears_timestamps_of_reopened_attempt_and_keeps_the_others(siap):
    # Arrange
    session_id, _run, failed, succeeded = _failed_run(siap)
    kept = _stamps(succeeded)

    # Act
    response = siap.sebagai(Role.ADMIN).post(
        f"/measurements/{session_id}/retry", json={"all_failed": True}
    )

    # Assert
    assert response.status_code == 202
    siap.db.expire_all()
    reopened = siap.db.get(Attempt, failed.id)
    assert reopened.status == AttemptStatus.PENDING
    assert (reopened.started_at, reopened.finished_at) == (None, None)
    assert _stamps(siap.db.get(Attempt, attempt_id) for attempt_id in kept) == kept


def test_rerun_gives_retried_attempt_fresh_timestamps_and_never_touches_successes(siap):
    # Arrange
    session_id, run, failed, succeeded = _failed_run(siap)
    first_finish = _utc(failed.finished_at)
    kept = _stamps(succeeded)
    siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/retry", json={"all_failed": True})

    # Act
    run()

    # Assert
    siap.db.expire_all()
    retried = siap.db.get(Attempt, failed.id)
    assert retried.status == AttemptStatus.SUCCEEDED
    assert first_finish < _utc(retried.started_at) <= _utc(retried.finished_at)
    assert _stamps(siap.db.get(Attempt, attempt_id) for attempt_id in kept) == kept
