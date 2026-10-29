import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.auth.models import User
from app.modules.auth.seeds import (
    ADMIN_SUB,
    AUTHOR_SUB,
    DEVELOPER_AI_SUB,
    REVIEWER_SUB,
    UNKNOWN_SUB,
    VIEWER_SUB,
)
from tests.login import complete_login

NON_ADMIN_SUBS = [AUTHOR_SUB, REVIEWER_SUB, VIEWER_SUB, DEVELOPER_AI_SUB]
NON_ADMIN_IDS = ["author", "reviewer", "viewer", "developer-ai"]
AUTHOR_ONLY = "/_test/rbac/author"


def _user(db_session, sub: str) -> User:
    db_session.expire_all()
    return db_session.query(User).filter(User.zitadel_sub == sub).one()


def _target_sub(actor_sub: str) -> str:
    # The member being acted on is never the actor.
    return AUTHOR_SUB if actor_sub == VIEWER_SUB else VIEWER_SUB


def _admin_client(db_session) -> TestClient:
    # A second browser with its own cookie jar, logged in as admin.
    admin = TestClient(app)
    complete_login(admin, db_session, ADMIN_SUB)
    return admin


class TestNonAdminCannotManageMembers:
    @pytest.mark.parametrize("actor_sub", NON_ADMIN_SUBS, ids=NON_ADMIN_IDS)
    def test_list_members_is_forbidden(self, client, db_session, actor_sub):
        complete_login(client, db_session, actor_sub)
        response = client.get("/admin/users")
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    @pytest.mark.parametrize("actor_sub", NON_ADMIN_SUBS, ids=NON_ADMIN_IDS)
    def test_create_member_is_forbidden_and_nobody_is_added(self, client, db_session, actor_sub):
        complete_login(client, db_session, actor_sub)
        before = db_session.query(User).count()
        response = client.post(
            "/admin/users",
            json={"email": "intruder@veritask.ai", "name": "Intruder", "role": "admin"},
        )
        assert response.status_code == 403
        assert db_session.query(User).count() == before

    @pytest.mark.parametrize("actor_sub", NON_ADMIN_SUBS, ids=NON_ADMIN_IDS)
    def test_promote_self_to_admin_is_forbidden(self, client, db_session, actor_sub):
        complete_login(client, db_session, actor_sub)
        actor = _user(db_session, actor_sub)
        response = client.patch(f"/admin/users/{actor.id}", json={"role": "admin"})
        assert response.status_code == 403
        assert _user(db_session, actor_sub).role == actor.role

    @pytest.mark.parametrize("actor_sub", NON_ADMIN_SUBS, ids=NON_ADMIN_IDS)
    def test_change_another_members_role_is_forbidden(self, client, db_session, actor_sub):
        complete_login(client, db_session, actor_sub)
        target = _user(db_session, _target_sub(actor_sub))
        response = client.patch(f"/admin/users/{target.id}", json={"role": "admin"})
        assert response.status_code == 403
        assert _user(db_session, _target_sub(actor_sub)).role == target.role

    @pytest.mark.parametrize("actor_sub", NON_ADMIN_SUBS, ids=NON_ADMIN_IDS)
    def test_deactivate_another_member_is_forbidden(self, client, db_session, actor_sub):
        complete_login(client, db_session, actor_sub)
        target = _user(db_session, _target_sub(actor_sub))
        response = client.post(f"/admin/users/{target.id}/deactivate")
        assert response.status_code == 403
        assert _user(db_session, _target_sub(actor_sub)).is_active is True


class TestRoleChangeAppliesToTheOpenSession:
    def test_demoted_member_loses_rights_without_logging_out(self, client, db_session):
        complete_login(client, db_session, AUTHOR_SUB)
        assert client.get(AUTHOR_ONLY).status_code == 200

        author = _user(db_session, AUTHOR_SUB)
        with _admin_client(db_session) as admin:
            changed = admin.patch(f"/admin/users/{author.id}", json={"role": "viewer"})
        assert changed.status_code == 200

        response = client.get(AUTHOR_ONLY)
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    def test_promoted_member_gains_rights_without_logging_in_again(self, client, db_session):
        complete_login(client, db_session, VIEWER_SUB)
        assert client.get(AUTHOR_ONLY).status_code == 403

        viewer = _user(db_session, VIEWER_SUB)
        with _admin_client(db_session) as admin:
            changed = admin.patch(f"/admin/users/{viewer.id}", json={"role": "author"})
        assert changed.status_code == 200

        assert client.get(AUTHOR_ONLY).status_code == 200


class TestDeactivationKillsTheOpenSession:
    def test_deactivated_members_cookie_is_rejected(self, client, db_session):
        complete_login(client, db_session, AUTHOR_SUB)
        assert client.get("/me").status_code == 200

        author = _user(db_session, AUTHOR_SUB)
        with _admin_client(db_session) as admin:
            assert admin.post(f"/admin/users/{author.id}/deactivate").status_code == 200

        response = client.get("/me")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"

    def test_reactivation_does_not_revive_the_old_cookie(self, client, db_session):
        complete_login(client, db_session, AUTHOR_SUB)
        author = _user(db_session, AUTHOR_SUB)
        with _admin_client(db_session) as admin:
            assert admin.post(f"/admin/users/{author.id}/deactivate").status_code == 200
            assert admin.post(f"/admin/users/{author.id}/activate").status_code == 200

        assert client.get("/me").status_code == 401


class TestRolePayloadIsStrict:
    @pytest.mark.parametrize(
        "payload",
        [
            {"role": "superadmin"},
            {"role": "ADMIN"},
            {"role": ""},
            {"role": None},
            {},
        ],
        ids=["unknown", "wrong-case", "blank", "null", "missing"],
    )
    def test_invalid_role_is_rejected_and_nothing_changes(self, client, db_session, payload):
        complete_login(client, db_session, ADMIN_SUB)
        viewer = _user(db_session, VIEWER_SUB)
        response = client.patch(f"/admin/users/{viewer.id}", json=payload)
        assert response.status_code == 422
        assert _user(db_session, VIEWER_SUB).role == "viewer"

    def test_role_update_ignores_every_field_except_role(self, client, db_session):
        complete_login(client, db_session, ADMIN_SUB)
        viewer = _user(db_session, VIEWER_SUB)
        email, name = viewer.email, viewer.name
        response = client.patch(
            f"/admin/users/{viewer.id}",
            json={
                "role": "author",
                "is_active": False,
                "email": "attacker@evil.test",
                "name": "Hijacked",
                "zitadel_sub": UNKNOWN_SUB,
            },
        )
        assert response.status_code == 200

        after = _user(db_session, VIEWER_SUB)
        assert after.role == "author"
        assert after.is_active is True
        assert (after.email, after.name) == (email, name)

    def test_create_member_cannot_preset_login_link_or_status(self, client, db_session):
        complete_login(client, db_session, ADMIN_SUB)
        response = client.post(
            "/admin/users",
            json={
                "email": "fresh@veritask.ai",
                "name": "Fresh",
                "role": "viewer",
                "zitadel_sub": UNKNOWN_SUB,
                "is_active": False,
            },
        )
        assert response.status_code == 201

        db_session.expire_all()
        created = db_session.query(User).filter(User.email == "fresh@veritask.ai").one()
        assert created.zitadel_sub is None
        assert created.is_active is True
