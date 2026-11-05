"""Passive requests must not slide the idle clock (SCRUM-251).

Observes HTTP status, the session cookie, response headers, and the sessions row.
Does not call resolve_session.
"""

import json
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
IDLE_TIMEOUT_HEADER = "x-veritask-idle-timeout"
IDLE_DEADLINE_HEADER = "x-veritask-idle-deadline"
ABSOLUTE_EXPIRES_HEADER = "x-veritask-absolute-expires-at"
CLIENT_DEADLINE = "1999-01-01T00:00:00+00:00"


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


def _assert_deadlines(response, *, last_activity: datetime, expires: datetime) -> None:
    settings = get_settings()
    assert response.headers.get(IDLE_TIMEOUT_HEADER) == str(settings.idle_timeout_minutes * 60)
    deadline = response.headers.get(IDLE_DEADLINE_HEADER)
    absolute = response.headers.get(ABSOLUTE_EXPIRES_HEADER)
    assert deadline is not None
    assert absolute is not None
    assert _aware(datetime.fromisoformat(deadline)) == last_activity + timedelta(
        minutes=settings.idle_timeout_minutes
    )
    assert _aware(datetime.fromisoformat(absolute)) == expires


def test_active_me_exposes_configured_deadlines(client, db_session, monkeypatch):
    complete_login(client, db_session, AUTHOR_SUB)
    session = _session(db_session)
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    frozen = start + timedelta(minutes=5)
    clock = _install_clock(monkeypatch)
    clock["now"] = frozen

    response = client.get("/me")

    assert response.status_code == 200
    db_session.refresh(session)
    assert _aware(session.last_activity_at) == frozen
    assert _aware(session.expires_at) == expires
    _assert_deadlines(response, last_activity=frozen, expires=expires)


def test_passive_me_exposes_deadlines_without_sliding(client, db_session, monkeypatch):
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
    _assert_deadlines(response, last_activity=start, expires=expires)


def test_client_supplied_deadline_does_not_change_server_deadline(client, db_session, monkeypatch):
    complete_login(client, db_session, AUTHOR_SUB)
    session = _session(db_session)
    start = _aware(session.last_activity_at)
    expires = _aware(session.expires_at)
    clock = _install_clock(monkeypatch)
    clock["now"] = start + timedelta(minutes=5)

    response = client.get(
        "/me",
        headers={ACTIVITY_HEADER: PASSIVE, "X-Veritask-Idle-Deadline": CLIENT_DEADLINE},
    )

    assert response.status_code == 200
    db_session.refresh(session)
    assert _aware(session.expires_at) == expires
    assert response.headers.get(IDLE_DEADLINE_HEADER) != CLIENT_DEADLINE
    _assert_deadlines(response, last_activity=start, expires=expires)


def test_openapi_names_activity_header_on_session_reads(client):
    spec = client.get("/openapi.json").json()
    for path in ("/me", "/auth/done"):
        operation = json.dumps(spec["paths"][path]["get"])
        assert ACTIVITY_HEADER in operation
        assert "X-Veritask-Idle-Timeout" in operation
        assert "X-Veritask-Idle-Deadline" in operation
        assert "X-Veritask-Absolute-Expires-At" in operation


def test_preflight_allows_activity_header_for_configured_origin(client):
    response = client.options(
        "/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": ACTIVITY_HEADER,
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-headers") == ACTIVITY_HEADER


def test_cors_exposes_session_deadline_headers_to_frontend_origin(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    response = client.get("/me", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") == "true"
    exposed = {
        part.strip().lower()
        for part in response.headers.get("access-control-expose-headers", "").split(",")
        if part.strip()
    }
    assert {
        IDLE_TIMEOUT_HEADER,
        IDLE_DEADLINE_HEADER,
        ABSOLUTE_EXPIRES_HEADER,
    } <= exposed
    assert response.headers.get(IDLE_TIMEOUT_HEADER)
    assert response.headers.get(IDLE_DEADLINE_HEADER)
    assert response.headers.get(ABSOLUTE_EXPIRES_HEADER)


def test_cors_withholds_allow_origin_from_foreign_origin(client, db_session):
    # Starlette attaches Access-Control-Expose-Headers on every response.
    # Without Access-Control-Allow-Origin the browser still cannot read them.
    complete_login(client, db_session, AUTHOR_SUB)
    response = client.get("/me", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") is None


def test_preflight_rejects_foreign_origin(client):
    response = client.options(
        "/me",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": ACTIVITY_HEADER,
        },
    )
    assert response.status_code == 400
    assert response.headers.get("access-control-allow-origin") is None
