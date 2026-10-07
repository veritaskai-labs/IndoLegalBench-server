"""Bentuk request dan response modul providers.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.providers.models import ConnectionTestErrorCategory, LastTestStatus, ProviderType


def _teks_bersih(value: str) -> str:
    bersih = value.strip()
    if not bersih:
        raise ValueError("tidak boleh kosong")
    return bersih


def _base_url_http(value: str) -> str:
    bersih = value.strip()
    parsed = urlparse(bersih)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url harus berupa URL http atau https")
    return bersih


class AiProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_type: ProviderType
    base_url: str = Field(min_length=1, max_length=2048)
    model_name: str = Field(min_length=1, max_length=200)
    credential: str = Field(min_length=4)
    rate_limit_per_minute: int = Field(gt=0)
    monthly_budget_idr: Decimal = Field(gt=0, max_digits=18, decimal_places=2)

    @field_validator("name", "model_name")
    @classmethod
    def wajib_ada(cls, value: str) -> str:
        return _teks_bersih(value)

    @field_validator("base_url")
    @classmethod
    def url_http(cls, value: str) -> str:
        return _base_url_http(value)


class AiProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    credential: str | None = Field(default=None, min_length=4)
    rate_limit_per_minute: int | None = Field(default=None, gt=0)
    monthly_budget_idr: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)

    @field_validator("name", "model_name")
    @classmethod
    def wajib_ada(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _teks_bersih(value)

    @field_validator("base_url")
    @classmethod
    def url_http(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _base_url_http(value)


class AiProductRead(BaseModel):
    """Kredensial tidak ada di sini. Hanya hint, dan penanda bahwa ciphertext tersimpan."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_type: ProviderType
    base_url: str
    model_name: str
    credential_hint: str
    has_credential: bool = True
    rate_limit_per_minute: int
    monthly_budget_idr: Decimal
    is_active: bool
    last_test_at: datetime | None
    last_test_status: LastTestStatus | None
    last_test_message: str | None
    last_test_error_category: ConnectionTestErrorCategory | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ConnectionTestRead(BaseModel):
    """Hasil uji koneksi. Kredensial tidak pernah ada di sini."""

    status: Literal["ok", "failed"]
    latency_ms: int | None = None
    message: str | None = None
    error_category: ConnectionTestErrorCategory | None = None
