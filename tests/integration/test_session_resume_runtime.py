"""AC8 end-to-end resume against the real dispatcher/store, including migrated PG."""

import pytest
from sqlalchemy import select

from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CallLog,
    MeasurementSession,
    SessionProductCopy,
)
from app.shared.security import Role
from tests.integration.test_session_control_runtime import session_logs
from tests.modules.conftest import as_role, buat_pengguna
from tests.modules.runs.test_execution_runtime import (
    Provider,
    db_session,
    jenis_peraturan,
    runner,
    start,
)
from tests.modules.runs.test_session_resume_endpoint import paused
from tests.modules.runs.test_start_endpoint import siap

__all__ = ["as_role", "buat_pengguna", "db_session", "jenis_peraturan", "siap"]


def test_first_resumed_response_mismatch_is_durable_and_keeps_paid_result(siap):
    session_id, provider, run = paused(siap)
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202
    original = provider.ask

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result.model_version = "actual-v2"
        return result

    provider.on_call = None
    provider.ask = changed
    assert run()
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "paused" and row.stop_reason == "error"
    assert row.resume_error_code == "MODEL_VERSION_CHANGED"
    assert not row.resume_version_check_pending
    assert len(provider.calls) == 2
    logs = session_logs(siap.db, session_id)
    assert len(logs) == 2 and all(log.cost_idr > 0 for log in logs)
    assert (
        siap.db.scalar(
            select(SessionProductCopy.model_version_at_start).where(
                SessionProductCopy.session_id == session_id
            )
        )
        == "model-v1"
    )
    assert (
        sum(
            a.status == AttemptStatus.SUCCEEDED
            for a in siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id))
        )
        == 2
    )
    detail = siap.client.get(f"/measurements/{session_id}").json()
    assert detail["resume_error_code"] == "MODEL_VERSION_CHANGED"
    response = siap.client.post(f"/measurements/{session_id}/resume")
    assert response.status_code == 409 and response.json()["code"] == "MODEL_VERSION_CHANGED"
    assert run() is False


def test_unknown_baseline_can_initialize_once_after_confirmed_resume(siap):
    session_id, provider, run = paused(siap, unknown=True)
    assert (
        siap.sebagai(Role.ADMIN)
        .post(f"/measurements/{session_id}/resume", json={"confirm_unverified_version": True})
        .status_code
        == 202
    )
    provider.on_call = None
    unknown_ask = provider.ask

    def observed(*args, **kwargs):
        result = unknown_ask(*args, **kwargs)
        result.model_version = "observed-v3"
        return result

    provider.ask = observed
    assert run()
    siap.db.expire_all()
    assert (
        siap.db.scalar(
            select(SessionProductCopy.model_version_at_start).where(
                SessionProductCopy.session_id == session_id
            )
        )
        == "observed-v3"
    )
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "finished" and row.model_version_unverified
    assert not row.resume_version_check_pending
    assert len(provider.calls) == 9


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_control_racing_with_mismatch_preserves_requested_stop_state(siap, action):
    session_id, provider, run = paused(siap)
    assert siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume").status_code == 202
    original = provider.ask

    def changed(*args, **kwargs):
        response = original(*args, **kwargs)
        response.model_version = "different-v2"
        return response

    provider.on_call = lambda: siap.client.post(f"/measurements/{session_id}/{action}")
    provider.ask = changed
    assert run()
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == ("paused" if action == "pause" else "cancelled")
    assert row.stop_reason == "user"
    assert row.resume_error_code == "MODEL_VERSION_CHANGED"
    assert len(provider.calls) == 2


def test_first_resumed_response_without_version_stays_explicitly_unverified(siap):
    session_id, provider, run = paused(siap)
    assert siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume").status_code == 202
    original = provider.ask

    def missing(*args, **kwargs):
        response = original(*args, **kwargs)
        response.model_version = ""
        return response

    provider.on_call = None
    provider.ask = missing
    assert run()
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "finished" and row.model_version_unverified
    assert row.resume_version_check_pending and row.resume_error_code is None
    assert (
        siap.db.scalar(
            select(SessionProductCopy.model_version_at_start).where(
                SessionProductCopy.session_id == session_id
            )
        )
        == "model-v1"
    )


def test_no_success_yet_allows_unknown_resume_without_confirmation(siap):
    session_id = start(siap)
    provider = Provider("error")
    original = provider.ask

    def missing(*args, **kwargs):
        response = original(*args, **kwargs)
        response.model_version = ""
        return response

    provider.ask = missing
    provider.on_call = lambda: siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/pause")
    run = runner(siap, provider)
    assert run()
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202 and response.json()["model_version_unverified"]
    provider.on_call = None
    assert run()
    assert len(provider.calls) == 9
    assert siap.db.get(MeasurementSession, session_id).status == "finished_with_failures"


def test_resume_does_not_retry_failed_work(siap):
    session_id = start(siap)
    provider = Provider("mixed")
    provider.on_call = lambda: (
        siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/pause")
        if len(provider.calls) == 2
        else None
    )
    run = runner(siap, provider)
    assert run()
    failed = siap.db.scalar(
        select(Attempt).where(
            Attempt.session_id == session_id, Attempt.status == AttemptStatus.TECHNICAL_FAILURE
        )
    )
    failure_log = siap.db.scalar(select(CallLog).where(CallLog.attempt_id == failed.id))
    assert siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume").status_code == 202
    provider.on_call = None
    assert run()
    siap.db.expire_all()
    assert siap.db.get(Attempt, failed.id).status == AttemptStatus.TECHNICAL_FAILURE
    assert siap.db.get(CallLog, failure_log.id).raw == failure_log.raw
    assert len(provider.calls) == 9
    assert siap.db.get(MeasurementSession, session_id).status == "finished_with_failures"


def test_version_evidence_uses_latest_success_across_sessions(siap):
    first, _, _ = paused(siap)
    second = start(siap)
    provider = Provider()
    original = provider.ask

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result.model_version = "newest-product-version"
        return result

    provider.ask = changed
    assert runner(siap, provider)()
    assert siap.db.get(MeasurementSession, second).status == "finished"
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{first}/resume")
    assert response.status_code == 409 and response.json()["code"] == "MODEL_VERSION_CHANGED"


def test_failed_attempt_cannot_supply_current_success_version(siap):
    first, _, _ = paused(siap)
    start(siap)
    provider = Provider()
    original = provider.ask

    def failed_without_kind(*args, **kwargs):
        result = original(*args, **kwargs)
        result.error = "provider failure"
        result.model_version = "failed-version-is-not-current-success"
        return result

    provider.ask = failed_without_kind
    assert runner(siap, provider)()
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{first}/resume")
    assert response.status_code == 202


def test_cancel_before_start_requeues_through_original_preparation(siap):
    session_id = start(siap)
    client = siap.sebagai(Role.ADMIN)
    assert client.post(f"/measurements/{session_id}/cancel").status_code == 202
    response = client.post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202 and response.json()["status"] == "queued"
    provider = Provider()
    assert runner(siap, provider)()
    assert len(provider.calls) == 9
    assert siap.db.get(MeasurementSession, session_id).status == "finished"


def test_orphan_running_attempt_is_not_resent(siap):
    session_id, _, _ = paused(siap)
    attempt = siap.db.scalar(
        select(Attempt).where(
            Attempt.session_id == session_id, Attempt.status == AttemptStatus.PENDING
        )
    )
    attempt.status = AttemptStatus.RUNNING
    siap.db.commit()
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 409 and response.json()["code"] == "ATTEMPT_RECOVERY_REQUIRED"
    assert siap.db.get(MeasurementSession, session_id).status == "paused"
    assert siap.db.get(Attempt, attempt.id).status == AttemptStatus.RUNNING


@pytest.mark.postgres
def test_rapid_pause_resume_during_active_call_does_not_duplicate_dispatch(siap):
    if siap.db.get_bind().dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL advisory lease required")
    session_id = start(siap)
    provider = Provider()

    def control():
        if len(provider.calls) != 1:
            return
        client = siap.sebagai(Role.ADMIN)
        assert client.post(f"/measurements/{session_id}/pause").status_code == 202
        assert client.post(f"/measurements/{session_id}/resume").status_code == 202
        # A competing dispatcher cannot claim another call while this one owns the lease.
        assert runner(siap, Provider())() is False

    provider.on_call = control
    assert runner(siap, provider)()
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "finished"
    assert len(provider.calls) == len(session_logs(siap.db, session_id)) == 9


@pytest.mark.parametrize("first_behavior", ["error", "ok"])
@pytest.mark.parametrize("observed", ["model-v1", "changed-v2"])
def test_versionless_response_keeps_guard_until_version_evidence(siap, first_behavior, observed):
    """Failure or success without a version cannot consume the resumed guard."""
    session_id, provider, run = paused(siap)
    assert siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume").status_code == 202
    original = provider.ask
    provider.on_call = None

    def sequenced(*args, **kwargs):
        # First resumed call is the session's second call overall.
        first_resumed = len(provider.calls) == 1
        if len(provider.calls) == 2:
            siap.db.expire_all()
            row = siap.db.get(MeasurementSession, session_id)
            assert row.resume_version_check_pending
        provider.behavior = first_behavior if first_resumed else "ok"
        response = original(*args, **kwargs)
        response.model_version = "" if first_resumed else observed
        return response

    provider.ask = sequenced
    assert run()
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    mismatch = observed != "model-v1"
    assert row.status == (
        "paused"
        if mismatch
        else "finished_with_failures"
        if first_behavior == "error"
        else "finished"
    )
    assert row.resume_error_code == ("MODEL_VERSION_CHANGED" if mismatch else None)
    assert row.model_version_unverified and not row.resume_version_check_pending
    assert len(provider.calls) == (3 if mismatch else 9)
    attempts = list(siap.db.scalars(select(Attempt).where(Attempt.session_id == session_id)))
    assert sum(a.status == AttemptStatus.TECHNICAL_FAILURE for a in attempts) == (
        first_behavior == "error"
    )
    assert sum(a.status == AttemptStatus.PENDING for a in attempts) == (6 if mismatch else 0)
    logs = session_logs(siap.db, session_id)
    assert len(logs) == len(provider.calls) and all(log.cost_idr > 0 for log in logs)
    if mismatch:
        assert row.stop_reason == "error"
        response = siap.client.post(f"/measurements/{session_id}/resume")
        assert response.status_code == 409 and response.json()["code"] == "MODEL_VERSION_CHANGED"
    assert run() is False
