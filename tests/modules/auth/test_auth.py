"""Fake-OIDC login flow tests (SCRUM-90).

- Covers authorize → callback → session cookie → /me → logout
- Error codes: `USER_NOT_REGISTERED`, `USER_DEACTIVATED`, `UNAUTHENTICATED`
- No Zitadel Cloud or PostgreSQL
"""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

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


def _complete_login(client, db_session, sub: str | None = None, email: str | None = None):
    # TODO: assumes AUTH_OIDC_MODE=fake; does not exercise Zitadel
    seed_users(db_session)
    params = {}
    if sub is not None:
        params["sub"] = sub
    if email is not None:
        params["email"] = email
    login = client.get("/auth/login", params=params, follow_redirects=False)
    assert login.status_code == 302
    authorize = client.get(login.headers["location"], follow_redirects=False)
    assert authorize.status_code == 302
    callback = client.get(authorize.headers["location"], follow_redirects=False)
    return callback


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
    callback = _complete_login(client, db_session, UNKNOWN_SUB)
    _assert_redirected_with_error(callback, "USER_NOT_REGISTERED")


def test_deactivated_user_returns_user_deactivated(client, db_session):
    """Test untuk pengguna yang dinonaktifkan. Logs in with a sub that is in users table but is not active."""
    callback = _complete_login(client, db_session, DEACTIVATED_SUB)
    _assert_redirected_with_error(callback, "USER_DEACTIVATED")


def test_null_sub_binds_on_matching_email(client, db_session):
    seed_users(db_session)
    pending = User(
        email="pending@veritask.test",
        name="Pending Author",
        role=Role.AUTHOR,
        zitadel_sub=None,
        is_active=True,
    )
    db_session.add(pending)
    db_session.commit()
    new_sub = "888888888888888888"
    callback = _complete_login(client, db_session, new_sub, email="pending@veritask.test")
    assert callback.status_code == 302
    db_session.refresh(pending)
    assert pending.zitadel_sub == new_sub


def test_email_with_different_sub_is_not_rebound(client, db_session):
    callback = _complete_login(client, db_session, UNKNOWN_SUB, email="author@veritask.test")
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
    callback = _complete_login(
        client, db_session, "777777777777777777", email="inactive-pending@veritask.test"
    )
    _assert_redirected_with_error(callback, "USER_DEACTIVATED")
    db_session.refresh(pending)
    assert pending.zitadel_sub is None


def test_login_sets_absolute_expires_at(client, db_session):
    callback = _complete_login(client, db_session, AUTHOR_SUB)
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
    callback = _complete_login(client, db_session, AUTHOR_SUB)
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


def test_me_without_cookie_is_unauthenticated(client):
    """Test untuk pengguna yang tidak memiliki sesi. Logs in without a session cookie."""
    me = client.get("/me")
    assert me.status_code == 401
    assert me.json()["code"] == "UNAUTHENTICATED"


def test_logout_clears_session_and_redirects_to_end_session(client, db_session):
    """Test untuk logout. Logs out and clears the session cookie."""
    callback = _complete_login(client, db_session, AUTHOR_SUB)
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
