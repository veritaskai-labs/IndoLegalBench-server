"""Pre-UAT F-08 (TC-30): an admin can reactivate a deactivated member.

POST /admin/users/{id}/activate is the undo of /deactivate. Reviewer
assignments voided at deactivation are NOT restored; the admin assigns
reviewers again from the monitoring page if needed.

- repository.activate_user: flips is_active back on
- AuthService.activate_member: 404 for unknown id, idempotent when already active
- end to end through fake-OIDC login and the real require_roles guard:
  admin 200, member can log in again, audit row user.activated,
  Author/Reviewer/Viewer 403, no session 401, unknown id 404,
  already active (incl. the admin themself) 200 with no new audit row
"""

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth import repository
from app.modules.auth.models import User
from app.modules.auth.seeds import (
    ADMIN_SUB,
    AUTHOR_SUB,
    DEACTIVATED_SUB,
    REVIEWER_SUB,
    VIEWER_SUB,
)
from app.modules.auth.service import AuthService
from app.shared.exceptions import NotFoundError
from app.shared.security import Role
from tests.login import complete_login


def _user(db_session, sub: str) -> User:
    return db_session.query(User).filter(User.zitadel_sub == sub).one()


def _user_actions(db_session, user_id: uuid.UUID) -> list[str]:
    rows = (
        db_session.query(AuditLog).filter(AuditLog.entity_id == user_id).order_by(AuditLog.id).all()
    )
    return [row.action for row in rows]


# --- repository ---------------------------------------------------------------


def test_repo_activate_user_sets_is_active(db_session):
    user = repository.create_user(
        db_session, name="Member", email="member@veritask.test", role=Role.REVIEWER
    )
    repository.deactivate_user(db_session, user)

    result = repository.activate_user(db_session, user)

    assert result.is_active is True
    assert repository.get_users(db_session, is_active=False) == []


# --- service ------------------------------------------------------------------


def test_service_activate_member_unknown_id_is_not_found(monkeypatch):
    monkeypatch.setattr("app.modules.auth.repository.get_user_by_id", lambda db, uid: None)

    with pytest.raises(NotFoundError):
        AuthService(MagicMock(spec=Session)).activate_member(uuid.uuid4())


def test_service_activate_member_already_active_does_not_write(monkeypatch):
    active = User(id=uuid.uuid4(), email="a@veritask.test", name="A", role=Role.AUTHOR)
    active.is_active = True
    monkeypatch.setattr("app.modules.auth.repository.get_user_by_id", lambda db, uid: active)
    activate = MagicMock()
    monkeypatch.setattr("app.modules.auth.repository.activate_user", activate)

    result = AuthService(MagicMock(spec=Session)).activate_member(active.id)

    assert result is active
    activate.assert_not_called()


# --- end to end ---------------------------------------------------------------


def test_admin_reactivates_member_who_can_log_in_again(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    member = _user(db_session, AUTHOR_SUB)
    assert client.post(f"/admin/users/{member.id}/deactivate").status_code == 200

    response = client.post(f"/admin/users/{member.id}/activate")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(member.id)
    assert body["is_active"] is True
    assert str(member.id) in [
        u["id"] for u in client.get("/admin/users", params={"is_active": "true"}).json()
    ]

    client.cookies.clear()
    callback = complete_login(client, db_session, AUTHOR_SUB)
    assert "error=" not in callback.headers["location"]
    assert client.get("/me").status_code == 200


def test_reactivation_is_audited_as_user_activated(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    member = _user(db_session, DEACTIVATED_SUB)

    client.post(f"/admin/users/{member.id}/activate")

    row = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == member.id, AuditLog.action == "user.activated")
        .one()
    )
    assert (row.before, row.after) == ({"is_active": False}, {"is_active": True})
    assert row.actor_role == "admin"


def test_already_active_member_is_idempotent_200_without_audit_row(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    member = _user(db_session, VIEWER_SUB)
    before = _user_actions(db_session, member.id)

    first = client.post(f"/admin/users/{member.id}/activate")
    second = client.post(f"/admin/users/{member.id}/activate")

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["is_active"] is True
    assert _user_actions(db_session, member.id) == before


def test_admin_activating_self_is_a_harmless_no_op(client, db_session):
    # A deactivated admin has no session, so the only self call that can
    # happen is on an active account, which stays as it is.
    complete_login(client, db_session, ADMIN_SUB)
    admin = _user(db_session, ADMIN_SUB)

    response = client.post(f"/admin/users/{admin.id}/activate")

    assert response.status_code == 200
    assert response.json()["is_active"] is True


def test_unknown_user_is_not_found(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)

    response = client.post(f"/admin/users/{uuid.uuid4()}/activate")

    assert response.status_code == 404


@pytest.mark.parametrize("sub", [AUTHOR_SUB, REVIEWER_SUB, VIEWER_SUB])
def test_non_admin_is_forbidden_and_member_stays_inactive(client, db_session, sub):
    complete_login(client, db_session, sub)
    member = _user(db_session, DEACTIVATED_SUB)

    response = client.post(f"/admin/users/{member.id}/activate")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    db_session.refresh(member)
    assert member.is_active is False


def test_no_session_is_unauthenticated(client):
    response = client.post(f"/admin/users/{uuid.uuid4()}/activate")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
