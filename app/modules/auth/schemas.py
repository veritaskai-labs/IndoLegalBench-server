"""Bentuk request dan response modul auth.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.
"""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.shared.security import Role


class UserResponse(BaseModel):
    id: uuid.UUID
    name: str
    # Plain str, not EmailStr: this is stored data going out, and EmailStr would
    # 500 the whole list on any row email-validator dislikes (e.g. `.test` seeds).
    email: str
    role: Role
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class UserCreateRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    role: Role

    @field_validator("email")
    @classmethod
    def _lowercase_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class UserUpdateRoleRequest(BaseModel):
    role: Role


class MeResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: Role


class ErrorBody(BaseModel):
    code: str
    message: str = Field(examples=["No platform account is mapped to this identity."])
    field: str | None = Field(
        default=None,
        description="Lokasi field yang gagal, misalnya legal_refs[0].pasal",
    )
