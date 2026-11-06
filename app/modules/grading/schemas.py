"""Bentuk request dan response modul grading.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini berubah, kontrak
API ikut berubah, jadi wajib diumumkan ke tim.

PBI-14 SCRUM-222 PR B: versi rubrik diketik Admin di aplikasi (AC3, AC7). Batas per
field ada di sini supaya ikut ke kontrak dan validasi client; aturan antar-field ada di
rubric_rules. version_no dan is_active tidak ada di body: server yang menentukan.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.modules.grading.models import GraderPath
from app.shared.text import strip_blank

# Batas yang sama dengan kolom tabel (migrasi ac845acfa98d).
MAX_ASPECTS = 20
MAX_LEVELS = 6
_PANJANG_NAMA = 200
_PANJANG_LABEL = 100


def _teks_wajib(value: str) -> str:
    """Buang karakter kosong di tepi (termasuk yang tak terlihat); sisa kosong ditolak."""
    bersih = strip_blank(value)
    if not bersih:
        raise ValueError("tidak boleh kosong")
    return bersih


TeksWajib = Annotated[str, AfterValidator(_teks_wajib)]


class _Body(BaseModel):
    """Field yang tidak dikenal ditolak, supaya salah ketik nama field tidak diam-diam hilang."""

    model_config = ConfigDict(extra="forbid")


class RubricAspectIn(_Body):
    """Satu dimensi rubrik. Bobot boleh kosong untuk rubrik holistik (K1)."""

    name: TeksWajib = Field(max_length=_PANJANG_NAMA)
    grader_path: GraderPath
    weight: Decimal | None = Field(default=None, gt=0, le=100, max_digits=5, decimal_places=2)
    definition: TeksWajib


class RubricDescriptorIn(_Body):
    """Deskriptor satu level untuk satu dimensi, dirujuk lewat nama dimensinya."""

    aspect_name: TeksWajib = Field(max_length=_PANJANG_NAMA)
    descriptor: TeksWajib


class RubricLevelIn(_Body):
    """Satu level skor 0–5 dengan penanda lulus (jawaban klien No. 48: lulus 4–5)."""

    score: int = Field(ge=0, le=5)
    label: TeksWajib = Field(max_length=_PANJANG_LABEL)
    is_passing: bool
    descriptors: list[RubricDescriptorIn] = Field(default_factory=list)


class RubricVersionCreate(_Body):
    """Isi lengkap versi rubrik baru. Prompt penilai ikut versi (K4), hanya untuk Admin."""

    effective_date: date
    judge_prompt_quality: TeksWajib
    judge_prompt_format: TeksWajib
    # K10: penyetuju dari tim hukum klien bukan akun aplikasi, jadi teks bebas.
    approved_by_name: TeksWajib | None = Field(default=None, max_length=_PANJANG_NAMA)
    approved_at: date | None = None
    aspects: list[RubricAspectIn] = Field(min_length=1, max_length=MAX_ASPECTS)
    levels: list[RubricLevelIn] = Field(min_length=1, max_length=MAX_LEVELS)


class RubricVersionSummary(BaseModel):
    """Satu baris daftar versi (SCRUM-228): nomor, tanggal, pembuat, penyetuju."""

    id: uuid.UUID
    version_no: int
    effective_date: date
    is_active: bool
    created_by: uuid.UUID
    # None kalau akun pembuat sudah tidak ada.
    created_by_name: str | None
    created_at: datetime
    approved_by_name: str | None
    approved_at: date | None
    aspect_count: int
    level_count: int


class RubricAspectRead(BaseModel):
    id: uuid.UUID
    name: str
    grader_path: GraderPath
    weight: Decimal | None
    definition: str
    sort_order: int


class RubricDescriptorRead(BaseModel):
    aspect_id: uuid.UUID
    aspect_name: str
    descriptor: str


class RubricLevelRead(BaseModel):
    id: uuid.UUID
    score: int
    label: str
    is_passing: bool
    descriptors: list[RubricDescriptorRead]


class RubricVersionRead(RubricVersionSummary):
    """Isi lengkap satu versi. Berisi prompt penilai, jadi hanya untuk Admin (K4, K7)."""

    locked_at: datetime | None
    judge_prompt_quality: str
    judge_prompt_format: str
    # Urut sort_order; level urut skor tertinggi dulu; deskriptor urut aspeknya.
    aspects: list[RubricAspectRead]
    levels: list[RubricLevelRead]
