"""Bentuk request dan response modul runs.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

TODO(PBI-11, PBI-12, PBI-13): definisikan schema sesuai kontrak.
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

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
