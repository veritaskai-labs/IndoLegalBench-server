"""Bentuk request dan response modul reports.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

PBI-15 SCRUM-320: bentuk baca laporan tersimpan. Isinya hasil beku dari
tabel D1 (reports, report_sessions, report_results, report_case_basis),
jadi membuka ulang laporan dan membuat PDF tidak pernah menghitung ulang.
Enum diambil dari reports/models.py (SCRUM-300) supaya nilai kolom dan
kontrak API tidak pernah berbeda.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.cases.models import SplitTag
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    ReportLanguage,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)


class ReportMarker(BaseModel):
    """Satu penanda D2 dari report_sessions.markers, misalnya 'repetitions_differ'."""

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
    # Uji Coba hanya disimpan di kolom ini, tidak diulang di markers (keputusan SCRUM-300).
    is_trial: bool = False
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
    product_label_mode: ProductLabelMode = ProductLabelMode.REAL_NAME
    rubric_version_no: int | None
    judge_model_name: str | None
    case_basis_count: int
    sessions: list[ReportSessionRead]
    case_basis: list[ReportCaseBasisRead]
