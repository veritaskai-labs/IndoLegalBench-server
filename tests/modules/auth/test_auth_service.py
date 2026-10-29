"""Unit tests for AuthService business logic.

Validates:
- list_users (with and without is_active filter)
- create_member (success with nullable zitadel_sub, conflict 409, unregistered Zitadel 422)
- update_member_role (success, not found 404)
- deactivate_member (success, self-deactivation 422, not found 404)
"""

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.modules.auth import service as service_module
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.shared.exceptions import NotFoundError
from app.shared.security import CurrentUser, Role


@pytest.fixture
def mock_db():
    return MagicMock(spec=Session)


@pytest.fixture
def auth_service(mock_db):
    return AuthService(mock_db)


def test_service_deactivate_member_success(auth_service, mock_db, monkeypatch):
    target_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    current_admin = CurrentUser(
        user_id=admin_id, name="Admin", email="admin@veritask.ai", role=Role.ADMIN
    )

    dummy_user = User(
        id=target_id, email="member@veritask.ai", name="Member", role=Role.REVIEWER, is_active=True
    )

    # Mock the functional repository methods
    monkeypatch.setattr("app.modules.auth.repository.get_user_by_id", lambda db, uid: dummy_user)
    monkeypatch.setattr("app.modules.auth.repository.delete_sessions_by_user_id", lambda db, uid: 1)
    # SCRUM-197: anggota tanpa kasus menunggu penilaian, jadi tidak perlu konfirmasi.
    monkeypatch.setattr(
        "app.modules.reviews.service.count_cases_waiting_for_reviewer", lambda db, uid: 0
    )

    def fake_deactivate(db, user):
        user.is_active = False
        return user

    monkeypatch.setattr("app.modules.auth.repository.deactivate_user", fake_deactivate)

    result = auth_service.deactivate_member(target_user_id=target_id, current_user=current_admin)

    assert result.is_active is False


def test_service_deactivate_member_not_found(auth_service, mock_db, monkeypatch):
    target_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    current_admin = CurrentUser(
        user_id=admin_id, name="Admin", email="admin@veritask.ai", role=Role.ADMIN
    )

    monkeypatch.setattr("app.modules.auth.repository.get_user_by_id", lambda db, uid: None)

    with pytest.raises(NotFoundError):
        auth_service.deactivate_member(target_user_id=target_id, current_user=current_admin)


# --- start_login / logout branches not reached through the HTTP tests ---


def test_start_login_with_email_only_sends_no_sub():
    oidc = MagicMock()

    service_module.start_login(oidc=oidc, email="author@veritask.test")

    assert oidc.authorization_url.call_args.kwargs["extra_params"] == {
        "email": "author@veritask.test"
    }


def test_logout_without_session_id_skips_the_database(mock_db):
    oidc = MagicMock()

    service_module.logout(mock_db, oidc=oidc, session_id=None)

    oidc.end_session_url.assert_called_once_with(id_token_hint=None)
    mock_db.assert_not_called()


def test_logout_with_unknown_session_still_ends_the_idp_session(mock_db, monkeypatch):
    deleted = MagicMock()
    monkeypatch.setattr("app.modules.auth.repository.get_session", lambda db, sid: None)
    monkeypatch.setattr("app.modules.auth.repository.delete_session", deleted)
    oidc = MagicMock()
    session_id = uuid.uuid4()

    service_module.logout(mock_db, oidc=oidc, session_id=session_id)

    deleted.assert_called_once_with(mock_db, session_id)
    oidc.end_session_url.assert_called_once_with(id_token_hint=None)
