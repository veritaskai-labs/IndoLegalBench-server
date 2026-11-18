"""Bentuk request dan response modul reports.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

PBI-15 SCRUM-320: bentuk baca laporan tersimpan. Isinya hasil beku dari
tabel D1 (reports, report_sessions, report_results, report_case_basis),
jadi membuka ulang laporan dan membuat PDF tidak pernah menghitung ulang.
Enum di bawah mengikuti nilai enum D1 apa adanya; tabelnya dibuat di
SCRUM-300 dan sebaiknya memakai enum yang sama dari file ini.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.modules.cases.models import SplitTag


class ScoreScope(StrEnum):
    """Jenis nilai yang dipilih saat membuat laporan (reports.score_scope)."""

    TEST = "test"
    DEV = "dev"
    DEV_TEST = "dev_test"


class ScoreSplit(StrEnum):
    """Satu baris report_results. Dev + Test menyimpan tiga baris per sesi (D1)."""

    TEST = "test"
    DEV = "dev"
    COMBINED = "combined"


class MeasurementFilter(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    ALL = "all"


class MeasurementKind(StrEnum):
    """Label otomatis/manual per produk, disalin dari measurement_sessions.session_type."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ReportStatus(StrEnum):
    SAVED = "saved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class ReportLanguage(StrEnum):
    """Bahasa laporan dan PDF-nya. Bawaan Inggris (PBI-26 AC1)."""

    EN = "en"
    ID = "id"


class ReportMarker(BaseModel):
    """Satu penanda D2 dari report_sessions.markers, misalnya 'trial' atau 'repetitions_differ'."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(examples=["repetitions_differ"])
    detail: str | None = Field(default=None, description="Keterangan tambahan, misalnya jumlah")


class ReportResultRead(BaseModel):
    """Satu baris report_results. Skor dua desimal, tanpa persen (AC skor)."""

    model_config = ConfigDict(from_attributes=True)

    score_split: ScoreSplit
    cases_graded_count: int
    cases_basis_count: int
    det_score: Decimal | None
    llm_score: Decimal | None
    total_score: Decimal | None = Field(description="Hanya untuk peringkat, tidak ditampilkan")
    failure_rate: Decimal | None = Field(description="Persen dari total upaya; NULL untuk manual")
    rank: int | None
    repetitions: int
    product_model_version: str | None
    judge_model_version: str | None


class ReportSessionRead(BaseModel):
    """Satu sesi (satu produk) di laporan beserta hasil bekunya."""

    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    product_id: uuid.UUID
    product_label: str = Field(description="Nama asli atau kode, dipilih Admin saat membuat")
    measurement_kind: MeasurementKind
    markers: list[ReportMarker] = Field(default_factory=list)
    results: list[ReportResultRead]


class ReportCaseBasisRead(BaseModel):
    """Satu kasus dasar perhitungan. Hanya kode dan versi, tanpa isi kasus (D9)."""

    model_config = ConfigDict(from_attributes=True)

    case_code: str
    case_version_no: int
    split_tag: SplitTag


class ReportSummary(BaseModel):
    """Satu baris di daftar laporan tersimpan (GET /reports)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    score_scope: ScoreScope
    has_dev_scores: bool
    status: ReportStatus
    language: ReportLanguage
    created_by: uuid.UUID
    created_at: datetime


class ReportDetail(ReportSummary):
    """Laporan lengkap untuk dibuka ulang dan dicetak ke PDF (GET /reports/{id})."""

    measurement_filter: MeasurementFilter
    rubric_version_no: int | None
    judge_model_name: str | None
    case_basis_count: int
    sessions: list[ReportSessionRead]
    case_basis: list[ReportCaseBasisRead]
