"""AC7: controls through HTTP while the real #98 dispatcher has a paid call in flight.

The provider alone is a stub. The database, snapshot, attempt claims, billing,
status service and finalizer are real; repeat against migrated PostgreSQL in CI.
"""

import pytest
from sqlalchemy import select

from app.modules.notifications.models import Notification
from app.modules.runs.models import Attempt, AttemptStatus, CallLog, MeasurementSession
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


def session_logs(db, session_id):
    return db.scalars(
        select(CallLog)
        .join(Attempt, Attempt.id == CallLog.attempt_id)
        .where(Attempt.session_id == session_id)
        .order_by(CallLog.started_at)
    ).all()


@pytest.mark.parametrize("action,target", [("pause", "paused"), ("cancel", "cancelled")])
@pytest.mark.parametrize("at_call", [1, 9])
def test_http_control_persists_active_call_and_wins_over_finalizer(siap, action, target, at_call):
    """Positive/edge: first or final call finishes, previous evidence never changes."""
    session_id = start(siap)
    client = siap.sebagai(Role.ADMIN)
    previous = []

    def control():
        if len(provider.calls) != at_call:
            return
        previous.extend(
            (log.id, log.attempt_id, log.cost_idr, log.raw)
            for log in session_logs(siap.db, session_id)
        )
        response = client.post(f"/measurements/{session_id}/{action}")
        assert response.status_code == 202
        assert response.json()["status"] == target

    provider = Provider(on_call=control)
    run = runner(siap, provider)
    assert run()
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == target
    assert len(provider.calls) == at_call
    logs = session_logs(siap.db, session_id)
    assert len(logs) == at_call
    assert all(log.cost_idr > 0 for log in logs)
    assert sum(log.cost_idr for log in logs) == logs[0].cost_idr * at_call
    assert len(previous) == at_call - 1
    assert all(
        any((log.id, log.attempt_id, log.cost_idr, log.raw) == old for log in logs)
        for old in previous
    )
    attempts = siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)).all()
    assert sum(a.status == AttemptStatus.SUCCEEDED for a in attempts) == at_call
    assert sum(a.status == AttemptStatus.PENDING for a in attempts) == 9 - at_call
    assert all(a.answer_text for a in attempts if a.status == AttemptStatus.SUCCEEDED)
    assert {log.attempt_id for log in logs} == {
        a.id for a in attempts if a.status == AttemptStatus.SUCCEEDED
    }
    assert (
        list(
            siap.db.scalars(select(Notification).where(Notification.link == f"/runs/{session_id}"))
        )
        == []
    )
    # The polling dispatcher must remain idle: no automatic resume or retry.
    assert run() is False
    assert len(provider.calls) == at_call


def test_cancel_queued_does_not_build_snapshot_or_call_provider(siap):
    """Edge: merged D4 permits cancelling queued work without paid dispatch."""
    from app.modules.runs.models import CaseSnapshot, SessionProductCopy

    session_id = start(siap)
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/cancel")
    assert response.status_code == 202
    provider = Provider()
    assert runner(siap, provider)() is False
    assert provider.calls == []
    for model in (CaseSnapshot, SessionProductCopy, Attempt):
        assert list(siap.db.scalars(select(model).where(model.session_id == session_id))) == []
    assert session_logs(siap.db, session_id) == []
