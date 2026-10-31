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

from app.modules.providers.models import (
    ConnectionTestErrorCategory,
    LastTestStatus,
    MeasurementType,
    ProviderType,
)

# Batas catatan akses dan parameter produk Manual (teks bebas, PBI-23 AC3).
_PANJANG_TEKS_MANUAL = 2000


def _teks_bersih(value: str) -> str:
    bersih = value.strip()
    if not bersih:
        raise ValueError("tidak boleh kosong")
    return bersih


def _url_http(value: str) -> str:
    bersih = value.strip()
    parsed = urlparse(bersih)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("harus berupa URL http atau https")
    return bersih


class ModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    effort: Literal["low", "medium", "high"] | None = None
    thinking: bool | None = None
    web_search: bool | None = None


class AiProductCreate(BaseModel):
    """Pendaftaran produk AI (PBI-10, PBI-23 AC1-AC3).

    Isian per jenis sengaja opsional di sini: wajib atau terlarangnya diputuskan
    rules.type_field_problem sesuai measurement_type, supaya kesalahan dilaporkan
    sebagai FIELD_REQUIRED / FIELD_NOT_ALLOWED dengan nama field-nya. Tanpa
    measurement_type produk dianggap Otomatis, sama seperti body PBI-10.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    measurement_type: MeasurementType = MeasurementType.AUTOMATIC
    # Otomatis
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    credential: str | None = Field(default=None, min_length=4)
    rate_limit_per_minute: int | None = Field(default=None, gt=0)
    monthly_budget_idr: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    model_params: ModelParams | None = None
    price_input_usd_per_mtok: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=4
    )
    price_output_usd_per_mtok: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=4
    )
    # Manual
    vendor_name: str | None = Field(default=None, min_length=1, max_length=200)
    site_url: str | None = Field(default=None, min_length=1, max_length=2048)
    # Teks bebas ikut tersalin ke audit_logs, jadi panjangnya dibatasi.
    access_note: str | None = Field(default=None, max_length=_PANJANG_TEKS_MANUAL)
    manual_params: str | None = Field(default=None, max_length=_PANJANG_TEKS_MANUAL)

    @field_validator("name")
    @classmethod
    def nama_wajib_ada(cls, value: str) -> str:
        return _teks_bersih(value)

    @field_validator("model_name", "vendor_name")
    @classmethod
    def teks_tidak_kosong(cls, value: str | None) -> str | None:
        return None if value is None else _teks_bersih(value)

    @field_validator("base_url", "site_url")
    @classmethod
    def url_http(cls, value: str | None) -> str | None:
        return None if value is None else _url_http(value)


class AiProductUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    measurement_type: MeasurementType | None = None
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    credential: str | None = Field(default=None, min_length=4)
    rate_limit_per_minute: int | None = Field(default=None, gt=0)
    monthly_budget_idr: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    model_params: ModelParams | None = None
    price_input_usd_per_mtok: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=4
    )
    price_output_usd_per_mtok: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=4
    )
    vendor_name: str | None = Field(default=None, min_length=1, max_length=200)
    site_url: str | None = Field(default=None, min_length=1, max_length=2048)
    access_note: str | None = Field(default=None, max_length=_PANJANG_TEKS_MANUAL)
    manual_params: str | None = Field(default=None, max_length=_PANJANG_TEKS_MANUAL)

    @field_validator("name", "model_name", "vendor_name")
    @classmethod
    def wajib_ada(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _teks_bersih(value)

    @field_validator("base_url", "site_url")
    @classmethod
    def url_http(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _url_http(value)


class AiProductRead(BaseModel):
    """Kredensial tidak ada di sini. Hanya hint, dan penanda bahwa ciphertext tersimpan."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    measurement_type: MeasurementType = MeasurementType.AUTOMATIC
    provider_type: ProviderType | None = None
    base_url: str | None = None
    model_name: str | None = None
    credential_hint: str | None = None
    has_credential: bool = False
    rate_limit_per_minute: int | None = None
    monthly_budget_idr: Decimal | None = None
    model_params: ModelParams | None = None
    price_input_usd_per_mtok: Decimal | None = None
    price_output_usd_per_mtok: Decimal | None = None
    vendor_name: str | None = None
    vendor_label: str | None = Field(
        default=None,
        description=(
            "Nama penyedia untuk daftar (PBI-23 AC6): vendor_name untuk produk Manual, "
            "label provider_type untuk produk Otomatis."
        ),
    )
    site_url: str | None = None
    access_note: str | None = None
    manual_params: str | None = None
    is_active: bool
    last_test_at: datetime | None = None
    last_test_status: LastTestStatus | None = None
    last_test_message: str | None = None
    last_test_error_category: ConnectionTestErrorCategory | None = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AiProductOption(BaseModel):
    """Satu pilihan produk di form pengukuran atau pencatatan manual (PBI-23 AC7, AC8).

    Sengaja hanya id, nama, dan jenis: Kontributor hanya boleh melihat nama produk
    manual aktif. Isian lengkap tetap di /admin/providers untuk Admin.
    """

    id: uuid.UUID
    name: str
    measurement_type: MeasurementType


class ConnectionTestRead(BaseModel):
    """Hasil uji koneksi. Kredensial tidak pernah ada di sini."""

    status: Literal["ok", "failed"]
    latency_ms: int | None = None
    message: str | None = None
    error_category: ConnectionTestErrorCategory | None = None
