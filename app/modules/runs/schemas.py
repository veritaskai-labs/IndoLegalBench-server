"""Bentuk request dan response modul runs.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

TODO(PBI-11, PBI-12, PBI-13): definisikan schema sesuai kontrak.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

# Satu enum status sesi untuk tabel (#89) dan kontrak API, supaya keduanya tidak menyimpang.
from app.modules.runs.models import SessionStatus

# PBI-11 AC3: satu angka pengulangan untuk seluruh pengukuran.
MIN_REPEATS = 1
MAX_REPEATS = 5
DEFAULT_REPEATS = 3


class CaseFilter(StrEnum):
    """Pilihan kasus yang ikut diukur (PBI-11 AC2). both berarti dev dan test."""

    DEV = "dev"
    TEST = "test"
    BOTH = "both"


class SummaryRequest(BaseModel):
    """Body POST /measurements/summary (PBI-11 AC2, AC3, AC5)."""

    suite_ids: list[uuid.UUID] = Field(
        min_length=1, description="Suite yang dipilih, minimal satu. Id ganda digabung."
    )
    case_filter: CaseFilter = Field(
        default=CaseFilter.BOTH, description="Kasus yang ikut: dev, test, atau both"
    )
    repeat_count: int = Field(
        default=DEFAULT_REPEATS,
        ge=MIN_REPEATS,
        le=MAX_REPEATS,
        description="Pengulangan per kasus, 1 sampai 5, sama untuk seluruh pengukuran",
    )
    product_id: uuid.UUID = Field(description="Satu produk AI otomatis yang diukur")

    @field_validator("case_filter", mode="before")
    @classmethod
    def filter_null_memakai_both(cls, value):
        return CaseFilter.BOTH if value is None else value

    @field_validator("repeat_count", mode="before")
    @classmethod
    def repeat_null_memakai_bawaan(cls, value):
        return DEFAULT_REPEATS if value is None else value

    @field_validator("suite_ids")
    @classmethod
    def gabungkan_suite_ganda(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class SummaryResponse(BaseModel):
    """Ringkasan sebelum menekan Mulai (PBI-11 AC5). Uang dikirim sebagai string."""

    case_count: int = Field(description="Kasus approved yang ikut, sesuai filter")
    total_calls: int = Field(description="Kasus kali pengulangan")
    case_filter: CaseFilter
    repeat_count: int
    estimated_cost_idr: Decimal = Field(
        description="Perkiraan biaya dalam rupiah, sudah termasuk margin pengaman"
    )
    remaining_budget_idr: Decimal = Field(description="Sisa anggaran bulan ini untuk produk")


# D2 measurement_sessions.label varchar(200).
_PANJANG_LABEL = 200


class MeasurementPurpose(StrEnum):
    """Pengukuran Resmi atau Uji Coba (PBI-11 AC1, D2 measurement_sessions.purpose)."""

    OFFICIAL = "official"
    TRIAL = "trial"


class StartRequest(SummaryRequest):
    """Body POST /measurements (PBI-11 AC1, SCRUM-207).

    Isian ringkasan diwarisi supaya Mulai divalidasi dengan aturan yang sama
    seperti Lihat ringkasan, ditambah label dan tujuan yang wajib.
    """

    label: str = Field(
        min_length=1, max_length=_PANJANG_LABEL, description="Nama sesi, wajib, maksimal 200"
    )
    purpose: MeasurementPurpose = Field(description="official = Pengukuran Resmi, trial = Uji Coba")

    @field_validator("label", mode="before")
    @classmethod
    def buang_spasi_tepi(cls, value):
        # Label berisi spasi saja menjadi kosong, lalu ditolak min_length.
        return value.strip() if isinstance(value, str) else value


class StartResponse(BaseModel):
    """Jawaban 201 tombol Mulai (D3 langkah 23)."""

    session_id: uuid.UUID
    status: SessionStatus = Field(description="Selalu queued: worker yang menjalankan saat giliran")
    # Nama D2 (keputusan PO di #89): antrean FIFO diurutkan dengan queued_at.
    queued_at: datetime = Field(description="Waktu masuk antrean; yang lebih awal jalan lebih dulu")
