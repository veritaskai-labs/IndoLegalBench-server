"""Unit tests for Auth Pydantic schemas.

Target: tests/modules/auth/test_auth_schemas.py
Validates:
- UserResponse serialization & attribute extraction
- UserCreateRequest validation (valid inputs, invalid email, invalid role, missing fields)
- UserUpdateRoleRequest validation
"""

import uuid

import pytest
from pydantic import ValidationError

from app.modules.auth.schemas import (
    UserCreateRequest,
    UserResponse,
    UserUpdateRoleRequest,
)
from app.shared.security import Role


# ----------------------------------------------------------------------
# 1. UserResponse Schemas
# ----------------------------------------------------------------------
def test_user_response_valid():
    user_id = uuid.uuid4()
    payload = {
        "id": user_id,
        "name": "Budi Legal",
        "email": "budi@veritask.ai",
        "role": Role.ADMIN,
        "is_active": True,
    }
    schema = UserResponse(**payload)
    assert schema.id == user_id
    assert schema.name == "Budi Legal"
    assert schema.email == "budi@veritask.ai"
    assert schema.role == Role.ADMIN
    assert schema.is_active is True


def test_user_response_from_attributes():
    """Ensures model_config ConfigDict(from_attributes=True) works with ORM-like objects."""

    class DummyUserORM:
        id = uuid.uuid4()
        name = "Sari Viewer"
        email = "sari@veritask.ai"
        role = Role.VIEWER
        is_active = False

    orm_obj = DummyUserORM()
    schema = UserResponse.model_validate(orm_obj)
    assert schema.id == orm_obj.id
    assert schema.name == "Sari Viewer"
    assert schema.email == "sari@veritask.ai"
    assert schema.role == Role.VIEWER
    assert schema.is_active is False


# ----------------------------------------------------------------------
# 2. UserCreateRequest Validation
# ----------------------------------------------------------------------
def test_user_create_request_valid():
    payload = {
        "email": "author@veritask.ai",
        "name": "Author Veritask",
        "role": "contributor",
    }
    req = UserCreateRequest(**payload)
    assert req.email == "author@veritask.ai"
    assert req.name == "Author Veritask"
    assert req.role == Role.CONTRIBUTOR


@pytest.mark.parametrize(
    "invalid_email",
    [
        "not-an-email",
        "missingatsign.com",
        "@missinguser.com",
        "user@",
        "",
    ],
)
def test_user_create_request_invalid_email(invalid_email):
    with pytest.raises(ValidationError):
        UserCreateRequest(email=invalid_email, name="Budi", role=Role.CONTRIBUTOR)


def test_user_create_request_invalid_role():
    with pytest.raises(ValidationError):
        UserCreateRequest(
            email="valid@veritask.ai",
            name="Budi",
            role="superadmin",  # Invalid platform role
        )


@pytest.mark.parametrize(
    "missing_field_payload",
    [
        {"email": "valid@veritask.ai", "role": Role.ADMIN},  # missing name
        {"name": "Budi", "role": Role.ADMIN},  # missing email
        {"email": "valid@veritask.ai", "name": "Budi"},  # missing role
        {},
    ],
)
def test_user_create_request_missing_required_fields(missing_field_payload):
    with pytest.raises(ValidationError):
        UserCreateRequest(**missing_field_payload)


# ----------------------------------------------------------------------
# 3. UserUpdateRoleRequest Validation
# ----------------------------------------------------------------------
def test_user_update_role_valid():
    req = UserUpdateRoleRequest(role=Role.CONTRIBUTOR)
    assert req.role == Role.CONTRIBUTOR


def test_user_update_role_invalid():
    with pytest.raises(ValidationError):
        UserUpdateRoleRequest(role="unauthorized_role")
