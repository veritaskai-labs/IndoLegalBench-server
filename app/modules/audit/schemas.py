"""Bentuk request dan response modul audit.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

PBI-18 AC3 dan AC4. Waktu tanpa zona dari Admin dianggap WIB, karena
itu jam yang dilihat di layar. Di dalam aplikasi semua waktu UTC.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.audit.models import AuditEntityType

WIB = timezone(timedelta(hours=7), "WIB")


def _ke_utc(value: datetime | None, *, tanpa_zona: timezone) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=tanpa_zona)
    return value.astimezone(UTC)


class AuditLogFilter(BaseModel):
    """Saringan pencarian Admin: rentang waktu, jenis data, atau nama pengguna."""

    occurred_from: datetime | None = Field(default=None, description="Batas awal, inklusif")
    occurred_to: datetime | None = Field(default=None, description="Batas akhir, inklusif")
    entity_type: AuditEntityType | None = None
    actor_id: uuid.UUID | None = None
    actor: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Sebagian nama atau email pelaku, tanpa peduli kapital",
    )
    case_id: uuid.UUID | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)

    @field_validator("occurred_from", "occurred_to")
    @classmethod
    def waktu_ke_utc(cls, value: datetime | None) -> datetime | None:
        return _ke_utc(value, tanpa_zona=WIB)

    @field_validator("actor")
    @classmethod
    def nama_tidak_kosong(cls, value: str | None) -> str | None:
        if value is None:
            return None
        bersih = value.strip()
        if not bersih:
            raise ValueError("Nama pelaku tidak boleh kosong")
        return bersih

    @model_validator(mode="after")
    def rentang_urut(self) -> Self:
        if self.occurred_from and self.occurred_to and self.occurred_from > self.occurred_to:
            raise ValueError("occurred_from harus sebelum atau sama dengan occurred_to")
        return self

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class AuditLogRead(BaseModel):
    """Satu catatan audit, lengkap untuk dibuka kembali sebagai bukti (AC4)."""

    id: int
    occurred_at: datetime
    actor_user_id: uuid.UUID | None = Field(description="Null berarti kejadian oleh sistem")
    actor_name: str | None
    actor_role: str | None
    action: str
    entity_type: AuditEntityType
    entity_id: uuid.UUID
    case_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    request_id: uuid.UUID | None

    @field_validator("occurred_at")
    @classmethod
    def tandai_utc(cls, value: datetime) -> datetime:
        # SQLite mengembalikan waktu tanpa zona, dan nilainya UTC.
        return _ke_utc(value, tanpa_zona=UTC)
