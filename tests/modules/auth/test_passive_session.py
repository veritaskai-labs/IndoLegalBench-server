"""Passive requests must not slide the idle clock (SCRUM-251).

Observes HTTP status, the session cookie, and the sessions row.
Does not call resolve_session.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.models import User, UserSession
from app.modules.auth.seeds import (
    ADMIN_SUB,
    AUTHOR_SUB,
    DEVELOPER_AI_SUB,
    VIEWER_SUB,
)
from app.shared.config import get_settings
from tests.login import complete_login

ACTIVITY_HEADER = "X-Veritask-Activity"
PASSIVE = "passive"


def _aware(stamp: datetime) -> datetime:
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp


def _install_clock(monkeypatch):
    clock = {"now": datetime.now(UTC)}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr("app.modules.auth.service.datetime", FrozenDateTime)
    return clock


def _session(db_session) -> UserSession:
    return db_session.query(UserSession).one()


def _assert_cookie_cleared(response) -> None:
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "veritask_session=" in set_cookie
    assert "max-age=0" in set_cookie or 'veritask_session=""' in set_cookie


def test_passive_request_leaves_activity_and_expiry_unchanged(client, db_session, monkeypatch):
    complete_login(client, db_session, AUTHOR_SUB)
    session = _session(db_session)
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    clock = _install_clock(monkeypatch)
    clock["now"] = start + timedelta(minutes=5)

    response = client.get("/me", headers={ACTIVITY_HEADER: PASSIVE})

    assert response.status_code == 200
    db_session.refresh(session)
    assert _aware(session.last_activity_at) == start
    assert _aware(session.expires_at) == expires


@pytest.mark.parametrize("sub", [AUTHOR_SUB, ADMIN_SUB, VIEWER_SUB, DEVELOPER_AI_SUB])
def test_passive_polls_still_expire_after_idle(client, db_session, monkeypatch, sub):
    complete_login(client, db_session, sub)
    session = _session(db_session)
    session_id = session.id
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    idle = get_settings().idle_timeout_minutes
    clock = _install_clock(monkeypatch)

    for minutes in (1, 5, 10):
        clock["now"] = start + timedelta(minutes=minutes)
        polled = client.get("/me", headers={ACTIVITY_HEADER: PASSIVE})
        assert polled.status_code == 200

    db_session.refresh(session)
    assert _aware(session.last_activity_at) == start
    assert _aware(session.expires_at) == expires

    clock["now"] = start + timedelta(minutes=idle, seconds=1)
    expired = client.get("/me", headers={ACTIVITY_HEADER: PASSIVE})
    assert expired.status_code == 401
    assert expired.json()["code"] == "SESSION_EXPIRED"
    assert db_session.get(UserSession, session_id) is None
    _assert_cookie_cleared(expired)


def test_passive_forbidden_role_does_not_refresh_activity(client, db_session, monkeypatch):
    complete_login(client, db_session, VIEWER_SUB)
    session = _session(db_session)
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    clock = _install_clock(monkeypatch)
    clock["now"] = start + timedelta(minutes=5)

    response = client.get("/_test/rbac/admin", headers={ACTIVITY_HEADER: PASSIVE})

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    db_session.refresh(session)
    assert _aware(session.last_activity_at) == start
    assert _aware(session.expires_at) == expires


def test_passive_allowed_role_does_not_refresh_activity(client, db_session, monkeypatch):
    complete_login(client, db_session, ADMIN_SUB)
    session = _session(db_session)
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    clock = _install_clock(monkeypatch)
    clock["now"] = start + timedelta(minutes=5)

    response = client.get("/_test/rbac/admin", headers={ACTIVITY_HEADER: PASSIVE})

    assert response.status_code == 200
    db_session.refresh(session)
    assert _aware(session.last_activity_at) == start
    assert _aware(session.expires_at) == expires


@pytest.mark.parametrize("value", ["background", "Passive", ""])
def test_unknown_activity_header_refreshes_last_activity(client, db_session, monkeypatch, value):
    complete_login(client, db_session, AUTHOR_SUB)
    session = _session(db_session)
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    frozen = start + timedelta(minutes=5)
    clock = _install_clock(monkeypatch)
    clock["now"] = frozen

    response = client.get("/me", headers={ACTIVITY_HEADER: value})

    assert response.status_code == 200
    db_session.refresh(session)
    assert _aware(session.last_activity_at) == frozen
    assert _aware(session.expires_at) == expires


def test_passive_request_past_absolute_cap_is_session_expired(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    session = _session(db_session)
    session_id = session.id
    session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    response = client.get("/me", headers={ACTIVITY_HEADER: PASSIVE})

    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_EXPIRED"
    assert db_session.get(UserSession, session_id) is None
    _assert_cookie_cleared(response)


def test_passive_request_for_deactivated_account_is_unauthenticated(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    session = _session(db_session)
    session_id = session.id
    user = db_session.get(User, session.user_id)
    user.is_active = False
    db_session.commit()

    response = client.get("/me", headers={ACTIVITY_HEADER: PASSIVE})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert db_session.get(UserSession, session_id) is None
