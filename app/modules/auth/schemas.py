"""Bentuk request dan response modul auth.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.
"""

import uuid

from pydantic import BaseModel, Field

from app.shared.security import Role


class MeResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: Role


class ErrorBody(BaseModel):
    code: str
    message: str = Field(examples=["No platform account is mapped to this identity."])
