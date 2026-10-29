"""SCRUM-91 require_role: 401 without session, 403 wrong role.

Uses pytest-only `/_test/rbac/*` probes, not PBI-2 `/suites`.
"""

from app.modules.auth.seeds import ADMIN_SUB, AUTHOR_SUB, VIEWER_SUB
from tests.login import complete_login


def test_require_role_without_session_is_unauthenticated(client):
    response = client.get("/_test/rbac/contributor")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_require_role_wrong_role_is_forbidden(client, db_session):
    complete_login(client, db_session, VIEWER_SUB)
    response = client.get("/_test/rbac/contributor")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_require_role_contributor_allowed(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    response = client.get("/_test/rbac/contributor")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_require_role_contributor_forbidden_on_admin(client, db_session):
    complete_login(client, db_session, AUTHOR_SUB)
    response = client.get("/_test/rbac/admin")
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_require_role_admin_allowed(client, db_session):
    complete_login(client, db_session, ADMIN_SUB)
    response = client.get("/_test/rbac/admin")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
