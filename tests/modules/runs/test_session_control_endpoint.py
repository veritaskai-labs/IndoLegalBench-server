"""AC7: HTTP controls use the real transition service; auth/clock isolated."""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.runs.models import SessionStatus, SessionType
from app.shared.security import Role
from tests.modules.runs.test_session_accept_endpoint import (
    _baca,
    _tandai,
    pembuat,
    produk,
)
from tests.modules.runs.test_session_list_endpoint import _sesi

# Reuse the existing product/user fixtures, without touching another ticket's files.
__all__ = ["pembuat", "produk"]
NOW = datetime(2026, 11, 5, 9, tzinfo=UTC)


@pytest.mark.parametrize("action,target", [("pause", "paused"), ("cancel", "cancelled")])
def test_admin_stops_and_reads_metadata(as_role, db_session, produk, pembuat, action, target):
    """Positive: both actions persist user stop metadata and detail can reload it."""
    row = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    client = as_role(Role.ADMIN)
    response = client.post(f"/measurements/{row.id}/{action}")
    assert response.status_code == 202
    assert response.json()["id"] == str(row.id)
    assert response.json()["status"] == target
    saved = _baca(db_session, row.id)
    assert saved.status == target
    assert saved.stop_reason == "user"
    assert saved.stopped_at is not None
    detail = client.get(f"/measurements/{row.id}").json()
    assert detail["status"] == target
    assert detail["stop_reason"] == "user"
    assert detail["stopped_at"] is not None


@pytest.mark.parametrize("action", ["pause", "cancel"])
@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER, Role.DEVELOPER_AI])
def test_non_admin_cannot_control(as_role, db_session, produk, pembuat, action, role):
    """Negative: no Dev AI ownership permission inferred from creation/retry rights."""
    row = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    assert as_role(role).post(f"/measurements/{row.id}/{action}").status_code == 403
    assert _baca(db_session, row.id).status == SessionStatus.RUNNING


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_anonymous_cannot_control(client, action):
    """Negative: real auth dependency rejects a request without a login cookie."""
    assert client.post(f"/measurements/{uuid.uuid4()}/{action}").status_code == 401


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_missing_session(as_role, action):
    response = as_role(Role.ADMIN).post(f"/measurements/{uuid.uuid4()}/{action}")
    assert response.status_code == 404
    assert response.json()["code"] == "MEASUREMENT_NOT_FOUND"


@pytest.mark.parametrize("action", ["pause", "cancel"])
@pytest.mark.parametrize("state", list(SessionStatus))
def test_transition_matrix_and_repeat(as_role, db_session, produk, pembuat, action, state):
    """Edge: pin merged D4, including invalid repeated requests and paused→cancelled."""
    row = _sesi(db_session, produk, pembuat, status=state)
    allowed = state == SessionStatus.RUNNING or (
        state in (SessionStatus.QUEUED, SessionStatus.PAUSED) and action == "cancel"
    )
    response = as_role(Role.ADMIN).post(f"/measurements/{row.id}/{action}")
    assert response.status_code == (202 if allowed else 409)
    if not allowed:
        assert response.json()["code"] == "INVALID_TRANSITION"
        assert _baca(db_session, row.id).status == state


@pytest.mark.parametrize("action", ["pause", "cancel"])
@pytest.mark.parametrize("guard", ["archived", "accepted", "manual"])
def test_guarded_session(as_role, db_session, produk, pembuat, action, guard):
    """Negative: reject guarded sessions before mutation; old evidence remains intact."""
    row = _sesi(db_session, produk, pembuat, status=SessionStatus.PAUSED)
    if guard == "archived":
        row.archived_at, row.archived_by = NOW, pembuat.id
    elif guard == "accepted":
        row.status = SessionStatus.FINISHED_WITH_FAILURES
        _tandai(db_session, row)
    else:
        row.session_type = SessionType.MANUAL
    db_session.commit()
    before = row.status
    response = as_role(Role.ADMIN).post(f"/measurements/{row.id}/{action}")
    assert response.status_code == 409
    assert (
        response.json()["code"]
        == {
            "archived": "SESSION_ARCHIVED",
            "accepted": "SESSION_ACCEPTED",
            "manual": "SESSION_NOT_AUTOMATIC",
        }[guard]
    )
    assert _baca(db_session, row.id).status == before


def test_fixed_clock_and_rollback(db_session, produk, pembuat, monkeypatch):
    """Edge: one transaction; persistence failure must not leave a partial control."""
    from app.modules.runs.session_control import stop_session

    row = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    result = stop_session(db_session, row.id, target=SessionStatus.PAUSED, now=NOW)
    assert result.stopped_at == NOW
    assert _baca(db_session, row.id).stopped_at.replace(tzinfo=UTC) == NOW
    # Reopen only through the existing service; this is not a resume endpoint/policy claim.
    from app.modules.runs.repository import SessionStatusRepository

    SessionStatusRepository(db_session).change_status(row.id, SessionStatus.RUNNING, at=NOW)

    def fail():
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(db_session, "commit", fail)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        stop_session(db_session, row.id, target=SessionStatus.CANCELLED, now=NOW)
    assert _baca(db_session, row.id).status == SessionStatus.RUNNING
