"""AC8 decisions supplied by Roben: remaining work, unknown confirmation and markers."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.runs.dependencies import build_session_queue
from app.modules.runs.models import CallLog, MeasurementSession, SessionProductCopy
from app.shared.config import Settings, get_settings
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.runs.test_execution_runtime import Provider, runner, start


def paused(siap, *, unknown=False):
    session_id = start(siap)
    provider = Provider()
    if unknown:
        original = provider.ask

        def ask(*args, **kwargs):
            response = original(*args, **kwargs)
            response.model_version = ""
            return response

        provider.ask = ask
    provider.on_call = lambda: siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/pause")
    run = runner(siap, provider)
    assert run()
    return session_id, provider, run


def test_resume_dispatches_only_remaining_work(siap):
    session_id, provider, run = paused(siap)
    previous = siap.db.scalar(select(CallLog))
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    provider.on_call = None
    assert run()
    assert len(provider.calls) == 9
    assert siap.db.get(CallLog, previous.id).cost_idr == previous.cost_idr
    assert len(list(siap.db.scalars(select(CallLog)))) == 9
    assert siap.db.get(MeasurementSession, session_id).status == "finished"


def test_unknown_success_requires_explicit_admin_confirmation(siap):
    session_id, _, _ = paused(siap, unknown=True)
    client = siap.sebagai(Role.ADMIN)
    url = f"/measurements/{session_id}/resume"
    response = client.post(url)
    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_VERSION_CONFIRMATION_REQUIRED"
    assert siap.db.get(MeasurementSession, session_id).status == "paused"
    response = client.post(url, json={"confirm_unverified_version": True})
    assert response.status_code == 202
    assert response.json()["model_version_unverified"] is True
    detail = client.get(f"/measurements/{session_id}").json()
    assert detail["model_version_unverified"] is True


@pytest.mark.parametrize("offset,marked", [(-1, False), (0, False), (1, True)])
def test_long_stop_boundary_uses_stopped_at(siap, monkeypatch, offset, marked):
    session_id, _, _ = paused(siap)
    now = datetime(2026, 11, 15, 10, tzinfo=UTC)
    row = siap.db.get(MeasurementSession, session_id)
    row.stopped_at = now - timedelta(days=7, seconds=offset)
    row.finished_at = now - timedelta(days=10)
    siap.db.commit()
    monkeypatch.setattr("app.modules.runs.session_control_router._now", lambda: now, raising=False)
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202
    assert response.json()["resumed_after_long_pause"] is marked
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.finished_at is None
    assert row.stopped_at is None
    assert row.stop_reason is None
    detail = siap.client.get(f"/measurements/{session_id}").json()
    assert detail["resumed_after_long_pause"] is marked
    assert detail["report_eligible"] is False


def test_resume_defaults_are_approved_and_flag_is_off():
    settings = Settings(_env_file=None)
    assert getattr(settings, "resume_long_stop_days", None) == 7
    assert getattr(settings, "measurement_dev_ai_controls_enabled", None) is False


def test_known_changed_version_rejects_atomically(siap):
    session_id, _, _ = paused(siap)
    # Synthetic conflicting baseline on SQLite; PG's immutable baseline is tested separately.
    copy = siap.db.scalar(select(SessionProductCopy))
    copy.model_version_at_start = "older-v0"
    siap.db.commit()
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_VERSION_CHANGED"
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "paused"


@pytest.mark.parametrize("action", ["pause", "cancel", "resume"])
def test_enabled_dev_ai_flag_never_grants_cross_owner_access(siap, action):
    session_id, _, _ = paused(siap)
    row = siap.db.get(MeasurementSession, session_id)
    row.created_by = USER_ID_QA
    siap.db.commit()
    settings = Settings(_env_file=None)
    # Pydantic rejects unknown settings until this approved flag is implemented.
    settings = settings.model_copy(update={"measurement_dev_ai_controls_enabled": True})
    app.dependency_overrides[get_settings] = lambda: settings
    response = siap.sebagai(Role.DEVELOPER_AI).post(f"/measurements/{session_id}/{action}")
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_YOUR_SESSION"


@pytest.mark.parametrize("action", ["pause", "cancel", "resume"])
def test_dev_ai_controls_only_own_session_when_flag_enabled(siap, action):
    session_id, _, _ = paused(siap)
    if action != "resume":
        assert (
            siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume").status_code == 202
        )
    settings = Settings(_env_file=None, measurement_dev_ai_controls_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings
    response = siap.sebagai(Role.DEVELOPER_AI).post(f"/measurements/{session_id}/{action}")
    assert response.status_code == 202


def test_dev_ai_cannot_confirm_missing_versions_even_when_flag_enabled(siap):
    session_id, _, _ = paused(siap, unknown=True)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, measurement_dev_ai_controls_enabled=True
    )
    response = siap.sebagai(Role.DEVELOPER_AI).post(
        f"/measurements/{session_id}/resume", json={"confirm_unverified_version": True}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_CONFIRMATION_REQUIRED"


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER, Role.DEVELOPER_AI])
def test_resume_denies_other_roles_by_default(siap, role):
    session_id, _, _ = paused(siap)
    assert siap.sebagai(role).post(f"/measurements/{session_id}/resume").status_code == 403


def test_resume_requires_login(client):
    import uuid

    assert client.post(f"/measurements/{uuid.uuid4()}/resume").status_code == 401


def test_occupied_slot_queues_resume_behind_the_running_session(siap):
    """SCRUM-267 AC8: an occupied slot puts the resumed session back in the queue."""
    session_id, _, _ = paused(siap)
    row = siap.db.get(MeasurementSession, session_id)
    old_queued = row.queued_at
    second = start(siap)
    assert build_session_queue(siap.db).take_next() == second
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "queued" and row.queued_at > old_queued
    assert row.stopped_at is None and row.stop_reason is None
    assert row.resume_version_check_pending
    assert siap.db.get(MeasurementSession, second).status == "running"


def test_unrelated_integrity_failure_is_not_misreported_as_slot_conflict(siap, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    session_id, _, _ = paused(siap)

    def broken():
        raise IntegrityError("synthetic statement", {}, ValueError("unrelated failure"))

    monkeypatch.setattr(siap.db, "commit", broken)
    with pytest.raises(IntegrityError):
        siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    assert row.status == "paused" and not row.resume_version_check_pending


def _slot_violation():
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    original = sqlite3.IntegrityError("UNIQUE constraint failed: measurement_sessions.session_type")
    original.sqlite_errorname = "SQLITE_CONSTRAINT_UNIQUE"
    return IntegrityError("synthetic statement", {}, original)


def test_racing_slot_violation_falls_back_to_the_queue(siap, monkeypatch):
    """SCRUM-267: a worker took the slot between the check and the commit."""
    session_id, _, _ = paused(siap)
    real_commit = siap.db.commit
    raised = []

    def lose_first_race():
        if not raised:
            raised.append(True)
            raise _slot_violation()
        real_commit()

    monkeypatch.setattr(siap.db, "commit", lose_first_race)
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202 and response.json()["status"] == "queued"
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "queued"


def test_scheduler_start_between_resume_checks_cannot_let_resume_jump(siap, monkeypatch):
    """SCRUM-267 K2: the worker starts the front Menunggu session between resume's two reads.

    another_running() still sees a free slot, then the worker commits the waiting session as
    running, so queue_waiting() sees an empty queue too. The real partial unique index must
    reject the stale running write and send the resumed session to the queue. One SQLite
    session plays both sides here; the two-connection version is in the PG race tests.
    """
    from app.modules.runs.session_control_repository import SessionControlRepository

    session_id, _, _ = paused(siap)
    waiting = start(siap)
    original = SessionControlRepository.another_running
    picked = []

    def scheduler_picks_front_session(self, resumed_id):
        result = original(self, resumed_id)
        if not picked:
            picked.append(build_session_queue(siap.db).take_next())
        return result

    monkeypatch.setattr(SessionControlRepository, "another_running", scheduler_picks_front_session)
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert picked == [waiting]
    assert response.status_code == 202 and response.json()["status"] == "queued"
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, waiting).status == "running"
    assert siap.db.get(MeasurementSession, session_id).status == "queued"


def test_repeated_slot_violation_rolls_back(siap, monkeypatch):
    """Defensive: a slot violation even on the queue path is a 409 with no writes."""
    session_id, _, _ = paused(siap)

    def broken():
        raise _slot_violation()

    monkeypatch.setattr(siap.db, "commit", broken)
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 409 and response.json()["code"] == "ANOTHER_SESSION_RUNNING"
    siap.db.expire_all()
    assert siap.db.get(MeasurementSession, session_id).status == "paused"


def test_repeat_resume_is_a_conflict_and_confirmation_is_strict_boolean(siap):
    session_id, _, _ = paused(siap)
    client = siap.sebagai(Role.ADMIN)
    url = f"/measurements/{session_id}/resume"
    assert client.post(url, json={"confirm_unverified_version": "true"}).status_code == 422
    assert client.post(url).status_code == 202
    response = client.post(url)
    assert response.status_code == 409 and response.json()["code"] == "SESSION_NOT_RESUMABLE"


def test_long_stop_marker_is_sticky_and_legacy_missing_stop_is_not_guessed(siap, monkeypatch):
    session_id, _, _ = paused(siap)
    row = siap.db.get(MeasurementSession, session_id)
    row.resumed_after_long_pause = True
    siap.db.commit()
    response = siap.sebagai(Role.ADMIN).post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202 and response.json()["resumed_after_long_pause"]
    assert siap.client.post(f"/measurements/{session_id}/pause").status_code == 202
    siap.db.expire_all()
    row = siap.db.get(MeasurementSession, session_id)
    row.resumed_after_long_pause = False
    row.stopped_at = None
    row.stop_reason = None
    siap.db.commit()
    response = siap.client.post(f"/measurements/{session_id}/resume")
    assert response.status_code == 202 and not response.json()["resumed_after_long_pause"]


def test_timezone_aware_stop_interval_and_custom_threshold():
    from datetime import timezone

    from app.modules.runs.session_control import _long_stop

    local = datetime(2026, 11, 5, 7, tzinfo=timezone(timedelta(hours=7)))
    assert not _long_stop(local, datetime(2026, 11, 7, tzinfo=UTC), 2)
    assert _long_stop(local, datetime(2026, 11, 7, 0, 0, 1, tzinfo=UTC), 2)


def test_negative_threshold_is_not_valid_configuration():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, resume_long_stop_days=-1)
