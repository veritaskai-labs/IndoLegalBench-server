"""Integration and route contract tests for Admin Member endpoints.

Target: tests/modules/auth/test_auth_router.py
Validates:
- GET /admin/users?is_active=
- POST /admin/users
- PATCH /admin/users/{id}
- POST /admin/users/{id}/deactivate
- RBAC & Security Boundary (require_roles(Role.ADMIN), A3, AC4)
"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.modules.auth.models import User
from app.modules.auth.router import get_auth_service
from app.modules.auth.router import router as admin_router
from app.modules.auth.service import AuthService
from app.shared.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.shared.security import CurrentUser, Role, get_current_user


@pytest.fixture
def app_instance():
    app = FastAPI()

    @app.exception_handler(DomainError)
    async def domain_exception_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code},
        )

    app.include_router(admin_router)
    return app


@pytest.fixture
def mock_service():
    return MagicMock(spec=AuthService)


@pytest.fixture
def admin_current_user():
    return CurrentUser(
        user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        name="Admin",
        email="admin@veritask.ai",
        role=Role.ADMIN,
    )


@pytest.fixture
def sample_user():
    return User(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        email="author@veritask.ai",
        name="Author Veritask",
        role=Role.CONTRIBUTOR,
        is_active=True,
    )


@pytest.fixture
def client(app_instance, mock_service, admin_current_user):
    app_instance.dependency_overrides[get_auth_service] = lambda: mock_service
    app_instance.dependency_overrides[get_current_user] = lambda: admin_current_user

    with TestClient(app_instance) as test_client:
        yield test_client

    app_instance.dependency_overrides.clear()


# --- GET /admin/users ---
def test_get_users_without_filter_returns_all(client, mock_service, sample_user):
    mock_service.list_users.return_value = [sample_user]

    response = client.get("/admin/users")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(sample_user.id)
    mock_service.list_users.assert_called_once_with(is_active=None)


@pytest.mark.parametrize("query_param,expected_bool", [("true", True), ("false", False)])
def test_get_users_with_is_active_filter(
    client, mock_service, sample_user, query_param, expected_bool
):
    mock_service.list_users.return_value = [sample_user]

    response = client.get(f"/admin/users?is_active={query_param}")

    assert response.status_code == status.HTTP_200_OK
    mock_service.list_users.assert_called_once_with(is_active=expected_bool)


def test_get_users_invalid_query_param_fails_422(client):
    response = client.get("/admin/users?is_active=not_a_boolean")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- POST /admin/users ---
def test_create_user_success_201(client, mock_service):
    new_id = uuid.uuid4()
    mock_service.create_member.return_value = User(
        id=new_id,
        email="newreviewer@veritask.ai",
        name="Reviewer Baru",
        role=Role.CONTRIBUTOR,
        is_active=True,
    )

    payload = {
        "email": "newreviewer@veritask.ai",
        "name": "Reviewer Baru",
        "role": "contributor",
    }
    response = client.post("/admin/users", json=payload)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["id"] == str(new_id)
    assert data["email"] == payload["email"]
    assert data["role"] == "contributor"
    assert data["is_active"] is True
    mock_service.create_member.assert_called_once()


def test_create_user_duplicate_email_conflict_409(client, mock_service):
    mock_service.create_member.side_effect = ConflictError(
        "Email 'clash@veritask.ai' already exists"
    )

    payload = {
        "email": "clash@veritask.ai",
        "name": "Clash User",
        "role": "contributor",
    }
    response = client.post("/admin/users", json=payload)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in response.json()["detail"]


def test_create_user_invalid_payload_fails_422(client):
    response = client.post("/admin/users", json={"email": "not-an-email"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- PATCH /admin/users/{id} ---
def test_update_user_role_success_200(client, mock_service, sample_user):
    sample_user.role = Role.CONTRIBUTOR
    mock_service.update_member_role.return_value = sample_user

    response = client.patch(
        f"/admin/users/{sample_user.id}",
        json={"role": "contributor"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["role"] == "contributor"
    mock_service.update_member_role.assert_called_once()


def test_update_user_role_not_found_404(client, mock_service):
    non_existent_id = uuid.uuid4()
    mock_service.update_member_role.side_effect = NotFoundError("User not found")

    response = client.patch(
        f"/admin/users/{non_existent_id}",
        json={"role": "admin"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "User not found"


def test_update_user_role_invalid_uuid_path_422(client):
    response = client.patch("/admin/users/invalid-uuid-string", json={"role": "contributor"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- POST /admin/users/{id}/deactivate ---
def test_deactivate_member_success_200(client, mock_service, sample_user):
    sample_user.is_active = False
    mock_service.deactivate_member.return_value = sample_user

    response = client.post(f"/admin/users/{sample_user.id}/deactivate")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is False


def test_deactivate_self_rejected_422(client, mock_service, admin_current_user):
    mock_service.deactivate_member.side_effect = ValidationError(
        "CANNOT_DEACTIVATE_SELF", code="CANNOT_DEACTIVATE_SELF"
    )

    response = client.post(f"/admin/users/{admin_current_user.user_id}/deactivate")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == "CANNOT_DEACTIVATE_SELF"
    assert response.json()["code"] == "CANNOT_DEACTIVATE_SELF"


def test_deactivate_user_not_found_404(client, mock_service):
    non_existent_id = uuid.uuid4()
    mock_service.deactivate_member.side_effect = NotFoundError("User not found")

    response = client.post(f"/admin/users/{non_existent_id}/deactivate")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# --- RBAC ---
def test_unauthenticated_request_rejected_401(app_instance, mock_service):
    app_instance.dependency_overrides[get_auth_service] = lambda: mock_service

    with TestClient(app_instance) as anon_client:
        response = anon_client.get("/admin/users")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("forbidden_role", [Role.CONTRIBUTOR, Role.CONTRIBUTOR, Role.VIEWER])
def test_non_admin_roles_rejected_403(app_instance, mock_service, forbidden_role):
    non_admin_user = CurrentUser(
        user_id=str(uuid.uuid4()),
        name="Member",
        email="user@veritask.ai",
        role=forbidden_role,
    )

    app_instance.dependency_overrides[get_auth_service] = lambda: mock_service
    app_instance.dependency_overrides[get_current_user] = lambda: non_admin_user

    with TestClient(app_instance) as non_admin_client:
        assert non_admin_client.get("/admin/users").status_code == status.HTTP_403_FORBIDDEN
        assert (
            non_admin_client.post("/admin/users", json={}).status_code == status.HTTP_403_FORBIDDEN
        )
        assert (
            non_admin_client.patch(f"/admin/users/{uuid.uuid4()}", json={}).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            non_admin_client.post(f"/admin/users/{uuid.uuid4()}/deactivate").status_code
            == status.HTTP_403_FORBIDDEN
        )

    app_instance.dependency_overrides.clear()
