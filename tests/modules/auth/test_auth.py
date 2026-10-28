"""Fake-OIDC login, idle timeout, and RBAC tests (SCRUM-90 / SCRUM-91).

Substitutes: fake IdP, SQLite, seed users, backdated last_activity_at.
Does not mock get_current_user. No Zitadel Cloud or PostgreSQL.
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app.modules.auth.models import User, UserSession
from app.modules.auth.seeds import (
    AUTHOR_ID,
    AUTHOR_SUB,
    DEACTIVATED_SUB,
    UNKNOWN_SUB,
    seed_users,
)
from app.shared.config import get_settings
from app.shared.security import Role
from tests.login import complete_login


def _assert_redirected_with_error(callback, code: str) -> None:
    """Callback yang gagal membelokkan ke /auth/done, bukan membalas JSON 403.

    Callback adalah navigasi halaman penuh yang datang dari IdP, jadi body
    JSON akan tampil mentah di layar pengguna. Kodenya dikirim sebagai query
    param supaya frontend (SCRUM-94) bisa menampilkan halaman error yang
    sesuai. Lihat komentar panjang di app/modules/auth/router.py.
    """
    assert callback.status_code == 302
    location = callback.headers["location"]
    assert location.startswith(get_settings().auth_done_url)
    assert parse_qs(urlparse(location).query)["error"] == [code]


def test_unregistered_sub_returns_user_not_registered(client, db_session):
    """Test untuk pengguna yang tidak terdaftar. Logs in with a sub that's not in users table."""
    callback = complete_login(client, db_session, UNKNOWN_SUB)
    _assert_redirected_with_error(callback, "USER_NOT_REGISTERED")


def test_deactivated_user_returns_user_deactivated(client, db_session):
    """Test untuk pengguna yang dinonaktifkan. Logs in with a sub that is in users table but is not active."""
    callback = complete_login(client, db_session, DEACTIVATED_SUB)
    _assert_redirected_with_error(callback, "USER_DEACTIVATED")


def test_null_sub_binds_on_matching_email(client, db_session):
    seed_users(db_session)
    pending = User(
        email="pending@veritask.test",
        name="Pending Author",
        role=Role.CONTRIBUTOR,
        zitadel_sub=None,
        is_active=True,
    )
    db_session.add(pending)
    db_session.commit()
    new_sub = "888888888888888888"
    callback = complete_login(client, db_session, new_sub, email="pending@veritask.test")
    assert callback.status_code == 302
    db_session.refresh(pending)
    assert pending.zitadel_sub == new_sub


def test_email_with_different_sub_is_not_rebound(client, db_session):
    callback = complete_login(client, db_session, UNKNOWN_SUB, email="author@veritask.test")
    _assert_redirected_with_error(callback, "USER_NOT_REGISTERED")


def test_deactivated_email_with_null_sub_stays_unbound(client, db_session):
    seed_users(db_session)
    pending = User(
        email="inactive-pending@veritask.test",
        name="Inactive Pending",
        role=Role.VIEWER,
        zitadel_sub=None,
        is_active=False,
    )
    db_session.add(pending)
    db_session.commit()
    callback = complete_login(
        client, db_session, "777777777777777777", email="inactive-pending@veritask.test"
    )
    _assert_redirected_with_error(callback, "USER_DEACTIVATED")
    db_session.refresh(pending)
    assert pending.zitadel_sub is None


def test_login_sets_absolute_expires_at(client, db_session):
    callback = complete_login(client, db_session, AUTHOR_SUB)
    assert callback.status_code == 302
    session = db_session.query(UserSession).one()
    created = session.last_activity_at
    expires = session.expires_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    delta_minutes = (expires - created).total_seconds() / 60
    assert abs(delta_minutes - get_settings().absolute_session_lifetime_minutes) < 1
    assert datetime.now(UTC) < expires


def test_happy_path_sets_httponly_samesite_cookie_and_me(client, db_session):
    """Test untuk pengguna yang terdaftar dan aktif. Logs in with a sub that is in users table and is active."""
    callback = complete_login(client, db_session, AUTHOR_SUB)
    assert callback.status_code == 302
    assert callback.headers["location"] == "http://localhost:3000/auth/done"

    set_cookie = callback.headers["set-cookie"].lower()
    assert "veritask_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    me = client.get("/me")
    assert me.status_code == 200
    body = me.json()
    assert body == {
        "id": str(AUTHOR_ID),
        "name": "Author One",
        "email": "author@veritask.test",
        "role": "author",
    }
    refreshed = me.headers.get("set-cookie", "").lower()
    assert "veritask_session=" in refreshed
    assert "max-age=" in refreshed


def test_session_cookie_outlives_idle_window(client, db_session):
    """Cookie max-age follows the absolute cap, not idle.

    If the cookie died with the idle window, an idle user would come back with
    no cookie and get UNAUTHENTICATED instead of SESSION_EXPIRED. TestClient does
    not age cookies, so the header is the only place to pin this.
    """
    settings = get_settings()
    callback = complete_login(client, db_session, AUTHOR_SUB)
    expected = f"max-age={settings.absolute_session_lifetime_minutes * 60}"
    assert expected in callback.headers["set-cookie"].lower()

    me = client.get("/me")
    assert expected in me.headers["set-cookie"].lower()
    assert settings.absolute_session_lifetime_minutes > settings.idle_timeout_minutes


def test_deactivated_mid_session_drops_session_row(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    session = db_session.query(UserSession).one()
    session_id = session.id
    user = db_session.get(User, session.user_id)
    user.is_active = False
    db_session.commit()

    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"
    assert db_session.get(UserSession, session_id) is None


def test_me_without_cookie_is_unauthenticated(client):
    """Test untuk pengguna yang tidak memiliki sesi."""
    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"


def test_me_with_invalid_cookie_is_unauthenticated(client):
    me = client.get("/me", headers={"Cookie": "veritask_session=not-a-uuid"})
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"


def test_auth_done_returns_me_payload(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    done = client.get("/auth/done")
    assert done.status_code == 200
    assert done.json()["role"] == "author"


def test_me_with_unknown_session_id_is_unauthenticated(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    client.cookies.set("veritask_session", str(uuid4()))
    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"


def test_stale_cookie_after_row_deleted_is_unauthenticated(client, db_session):
    callback = complete_login(client, db_session, AUTHOR_SUB)
    assert callback.status_code == 302
    session = db_session.query(UserSession).one()
    db_session.delete(session)
    db_session.commit()

    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"


def test_idle_timeout_returns_session_expired_and_clears_cookie(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    session = db_session.query(UserSession).one()
    session_id = session.id
    session.last_activity_at = datetime.now(UTC) - timedelta(
        minutes=get_settings().idle_timeout_minutes + 1
    )
    db_session.commit()

    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "SESSION_EXPIRED"
    assert db_session.get(UserSession, session_id) is None

    set_cookie = me.headers.get("set-cookie", "").lower()
    assert "veritask_session=" in set_cookie
    assert "max-age=0" in set_cookie or 'veritask_session=""' in set_cookie


def test_activity_inside_idle_window_slides_last_activity(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    session = db_session.query(UserSession).one()
    old = datetime.now(UTC) - timedelta(minutes=get_settings().idle_timeout_minutes - 1)
    session.last_activity_at = old
    db_session.commit()
    original_expires = session.expires_at
    if original_expires.tzinfo is None:
        original_expires = original_expires.replace(tzinfo=UTC)

    me = client.get("/me")
    assert me.status_code == 200
    db_session.refresh(session)
    last = session.last_activity_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    assert last > old
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    assert expires == original_expires


def test_absolute_expiry_returns_session_expired(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    session = db_session.query(UserSession).one()
    session_id = session.id
    session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "SESSION_EXPIRED"
    assert db_session.get(UserSession, session_id) is None


def test_logout_clears_session_and_redirects_to_end_session(client, db_session):
    """Test untuk logout. Logs out and clears the session cookie."""
    callback = complete_login(client, db_session, AUTHOR_SUB)
    assert callback.status_code == 302
    assert client.get("/me").status_code == 200

    logout = client.post("/auth/logout", follow_redirects=False)
    assert logout.status_code == 302
    location = logout.headers["location"]
    assert "/_fake/oidc/end_session" in location
    assert "post_logout_redirect_uri=" in location

    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"


def test_openapi_has_no_password_fields(client):
    """Test untuk OpenAPI spec. Checks that the spec does not contain password fields."""
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    dumped = json.dumps(spec.json()).lower()
    # TODO: substring check is weak (false positives/negatives vs schema fields)
    assert "password" not in dumped


def test_invalid_state_also_redirects_instead_of_returning_json(client):
    """Bukan hanya error status akun yang dibelokkan, tapi semua kegagalan.

    State yang hilang atau kedaluwarsa juga sampai ke pengguna lewat
    navigasi halaman penuh, jadi JSON mentah akan tetap terlihat kalau
    errornya dibiarkan naik ke handler global.
    """
    callback = client.get(
        "/auth/callback",
        params={"code": "apa-saja", "state": "state-yang-tidak-dikenal"},
        follow_redirects=False,
    )
    _assert_redirected_with_error(callback, "INVALID_OIDC_STATE")


def test_me_still_answers_with_json_not_a_redirect(client):
    """Batas perlakuan khusus itu ada di callback saja.

    Frontend memanggil /me lewat fetch dan membaca field code dari body,
    jadi endpoint ini harus tetap membalas JSON. Kalau suatu saat ikut
    dibelokkan, interceptor SESSION_EXPIRED di frontend berhenti bekerja.
    """
    me = client.get("/me", follow_redirects=False)

    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"
