"""Bentuk request dan response provider penilai (LLM judge). PBI-14 AC6, SCRUM-258.

Schema di sini ikut membentuk kontrak OpenAPI. Kredensial hanya ditulis: tidak ada
schema respons yang memuat ciphertext atau plaintext, hanya credential_hint dan
has_credential (L5, PBI-10 AC2).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.providers.models import ProviderType
from app.shared.text import strip_blank

_HARGA = {"ge": 0, "max_digits": 12, "decimal_places": 4}
_ANGGARAN = {"gt": 0, "max_digits": 18, "decimal_places": 2}
_KREDENSIAL = {"min_length": 4, "description": "Hanya ditulis; respons memuat hint 4 karakter"}


def _teks_wajib(value: str) -> str:
    bersih = strip_blank(value)
    if not bersih:
        raise ValueError("tidak boleh kosong")
    return bersih


def _url_http(value: str) -> str:
    bersih = strip_blank(value)
    parsed = urlparse(bersih)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("harus berupa URL http atau https")
    return bersih


class JudgeProviderCreate(BaseModel):
    """Pendaftaran penilai oleh Admin. Isian di luar D6 ditolak, tidak dibuang diam-diam."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    provider_type: ProviderType
    base_url: str = Field(max_length=2048)
    model_name: str = Field(max_length=200)
    credential: str = Field(**_KREDENSIAL)
    price_input_usd_per_mtok: Decimal = Field(**_HARGA)
    price_output_usd_per_mtok: Decimal = Field(**_HARGA)
    monthly_budget_idr: Decimal = Field(
        **_ANGGARAN, description="Anggaran bulanan penilai, terpisah dari produk (klien No. 63 A)"
    )
    is_active: bool = True

    @field_validator("name", "model_name")
    @classmethod
    def teks_tidak_kosong(cls, value: str) -> str:
        return _teks_wajib(value)

    @field_validator("base_url")
    @classmethod
    def url_http(cls, value: str) -> str:
        return _url_http(value)


class JudgeProviderUpdate(BaseModel):
    """PATCH dibatasi kredensial, harga, dan anggaran (keputusan PO K5, 7 Nov).

    Nama, jenis, URL, dan model dirujuk sesi lama, jadi tidak bisa diganti; aktif atau
    nonaktif lewat endpoint sendiri. Kolomnya NOT NULL, jadi null eksplisit ditolak.
    """

    model_config = ConfigDict(extra="forbid")

    credential: str | None = Field(default=None, **_KREDENSIAL)
    price_input_usd_per_mtok: Decimal | None = Field(default=None, **_HARGA)
    price_output_usd_per_mtok: Decimal | None = Field(default=None, **_HARGA)
    monthly_budget_idr: Decimal | None = Field(default=None, **_ANGGARAN)

    @field_validator("*", mode="before")
    @classmethod
    def tolak_null(cls, value):
        if value is None:
            raise ValueError("tidak boleh null; hilangkan isian ini kalau tidak diubah")
        return value


class JudgeProviderRead(BaseModel):
    """Satu penilai untuk Admin. Kredensial tidak ada di sini, hanya hint."""

    id: uuid.UUID
    name: str
    provider_type: ProviderType
    base_url: str
    model_name: str
    credential_hint: str
    has_credential: bool
    price_input_usd_per_mtok: Decimal
    price_output_usd_per_mtok: Decimal
    monthly_budget_idr: Decimal
    is_active: bool
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class JudgeProviderOption(BaseModel):
    """Satu pilihan penilai di form pengukuran (SCRUM-211). Tanpa URL, hint, dan harga."""

    id: uuid.UUID
    name: str
    provider_type: ProviderType
    model_name: str
