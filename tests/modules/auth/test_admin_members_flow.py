"""SCRUM-92 admin members, end to end through the real app.

The router tests mock AuthService and override get_current_user. These go
through fake-OIDC login, the real require_roles guard, and SQLite, so they
catch what the mocks cannot: the guard itself, the database, and sessions.
"""

from app.modules.auth.models import User, UserSession
from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB, DEVELOPER_AI_SUB
from tests.login import complete_login


def _admin(db_session) -> User:
    return db_session.query(User).filter(User.zitadel_sub == ADMIN_SUB).one()


def test_non_admin_is_forbidden_by_the_real_guard(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    response = client.get("/admin/users")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_no_session_is_unauthenticated(client):
    response = client.get("/admin/users")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_create_list_update_deactivate(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)

    created = client.post(
        "/admin/users",
        json={"email": "New.Member@Veritask.ai", "name": "  New Member  ", "role": "viewer"},
    )
    assert created.status_code == 201
    member = created.json()
    assert member["email"] == "new.member@veritask.ai"
    assert member["name"] == "New Member"
    assert member["is_active"] is True

    listed = client.get("/admin/users", params={"is_active": "true"})
    assert listed.status_code == 200
    assert member["id"] in [u["id"] for u in listed.json()]

    updated = client.patch(f"/admin/users/{member['id']}", json={"role": "contributor"})
    assert updated.status_code == 200
    assert updated.json()["role"] == "contributor"

    deactivated = client.post(f"/admin/users/{member['id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_duplicate_email_is_conflict_regardless_of_case(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    first = client.post(
        "/admin/users", json={"email": "dup@veritask.ai", "name": "Dup", "role": "contributor"}
    )
    assert first.status_code == 201
    response = client.post(
        "/admin/users", json={"email": "DUP@Veritask.ai", "name": "Dup", "role": "contributor"}
    )
    assert response.status_code == 409


def test_list_does_not_500_on_seed_emails(client, db_session):
    """Seeds use the reserved `.test` domain. The response must not re-validate them."""
    complete_login(client, db_session, ADMIN_SUB)
    response = client.get("/admin/users")
    assert response.status_code == 200
    assert "author@veritask.test" in [u["email"] for u in response.json()]


def test_blank_name_is_rejected(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    response = client.post(
        "/admin/users",
        json={"email": "blank@veritask.ai", "name": "   ", "role": "contributor"},
    )
    assert response.status_code == 422


def test_admin_cannot_change_own_role(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    admin_id = _admin(db_session).id
    response = client.patch(f"/admin/users/{admin_id}", json={"role": "viewer"})
    assert response.status_code == 422
    assert response.json()["code"] == "CANNOT_CHANGE_OWN_ROLE"
    db_session.expire_all()
    assert _admin(db_session).role == "admin"


def test_admin_cannot_deactivate_self(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    admin_id = _admin(db_session).id
    response = client.post(f"/admin/users/{admin_id}/deactivate")
    assert response.status_code == 422
    assert response.json()["code"] == "CANNOT_DEACTIVATE_SELF"


def test_deactivate_ends_the_members_sessions(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    author = db_session.query(User).filter(User.zitadel_sub == AUTHOR_SUB).one()
    assert db_session.query(UserSession).filter(UserSession.user_id == author.id).count() == 1

    client.cookies.clear()
    complete_login(client, db_session, ADMIN_SUB)
    response = client.post(f"/admin/users/{author.id}/deactivate")
    assert response.status_code == 200
    assert db_session.query(UserSession).filter(UserSession.user_id == author.id).count() == 0


def test_login_links_by_email_case_insensitively(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    client.post(
        "/admin/users",
        json={"email": "pending@veritask.ai", "name": "Pending", "role": "contributor"},
    )
    client.cookies.clear()

    callback = complete_login(client, db_session, "888888888888888888", email="Pending@Veritask.ai")
    assert callback.status_code == 302
    assert "error=" not in callback.headers["location"]
    assert client.get("/me").json()["email"].lower() == "pending@veritask.ai"


def test_admin_registers_a_developer_ai_member(client, db_session):
    """SCRUM-183: Developer AI goes through the same PBI-1 flow as every role."""
    complete_login(client, db_session, ADMIN_SUB)

    created = client.post(
        "/admin/users",
        json={"email": "dev.ai@veritask.ai", "name": "Dev AI", "role": "developer_ai"},
    )
    assert created.status_code == 201
    assert created.json()["role"] == "developer_ai"
    client.cookies.clear()

    callback = complete_login(client, db_session, "777777777777777777", email="dev.ai@veritask.ai")
    assert "error=" not in callback.headers["location"]
    assert client.get("/me").json()["role"] == "developer_ai"


def test_admin_changes_a_role_to_and_from_developer_ai(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    member = client.post(
        "/admin/users", json={"email": "swap@veritask.ai", "name": "Swap", "role": "viewer"}
    ).json()

    to_dev = client.patch(f"/admin/users/{member['id']}", json={"role": "developer_ai"})
    back = client.patch(f"/admin/users/{member['id']}", json={"role": "viewer"})

    assert to_dev.status_code == 200
    assert to_dev.json()["role"] == "developer_ai"
    assert back.json()["role"] == "viewer"


def test_seeded_developer_ai_logs_in_without_a_dashboard(client, db_session):
    complete_login(client, db_session, DEVELOPER_AI_SUB)

    assert client.get("/me").json()["role"] == "developer_ai"
    assert client.get("/dashboard").status_code == 403
