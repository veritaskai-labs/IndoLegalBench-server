"""Bentuk request dan response modul reports.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

PBI-15 SCRUM-79 SCRUM-313: POST /reports dan GET /reports/eligible-sessions. Aturan isian ditulis di sini sekali; frontend tidak
menyalinnya.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.auth.schemas import ErrorBody
from app.modules.reports.eligible_sessions import EligibleSession
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    ReportLanguage,
    ReportMarkerCode,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)
from app.modules.reports.report_generation import GeneratedReport, ReportRequest
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from app.shared.text import strip_blank

_TITLE_MAX = 200


class ReportCreate(BaseModel):
    """Permintaan membuat laporan perbandingan dari beberapa sesi pengukuran."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=_TITLE_MAX,
        description="Judul laporan. Karakter kosong di tepi, termasuk yang tak terlihat, dibuang.",
    )
    session_ids: list[uuid.UUID] = Field(
        min_length=1,
        description=(
            "Sesi yang dibandingkan, satu sesi per produk, urut seperti dikirim. Tidak ada "
            "batas jumlah. Minimal dua produk dicek server (409 NEED_AT_LEAST_TWO_PRODUCTS)."
        ),
    )
    score_scope: ScoreScope = Field(
        description=(
            "Jenis nilai: test, dev, atau dev_test. dev dan dev_test hanya untuk Admin dan "
            "Developer AI; peran lain mendapat 403 REPORT_SCOPE_FORBIDDEN."
        )
    )
    measurement_filter: MeasurementFilter = Field(
        default=MeasurementFilter.ALL,
        description="Saring sesi otomatis atau manual saja; bawaannya semuanya.",
    )
    product_label_mode: ProductLabelMode = Field(
        default=ProductLabelMode.REAL_NAME,
        description=(
            "Nama asli (bawaan) atau kode Product A, B, ... Hanya Admin yang boleh memakai kode "
            "(403 REPORT_LABEL_MODE_FORBIDDEN)."
        ),
    )
    language: ReportLanguage = Field(
        default=ReportLanguage.EN, description="Bahasa laporan dan PDF-nya."
    )

    @field_validator("title", mode="before")
    @classmethod
    def buang_karakter_kosong_di_tepi(cls, value):
        # Judul berisi karakter kosong saja (termasuk zero-width) menjadi kosong, lalu ditolak
        # min_length; CHECK database memakai daftar karakter yang sama.
        return strip_blank(value) if isinstance(value, str) else value

    def to_request(self) -> ReportRequest:
        return ReportRequest(
            title=self.title,
            session_ids=tuple(self.session_ids),
            score_scope=self.score_scope,
            measurement_filter=self.measurement_filter,
            product_label_mode=self.product_label_mode,
            language=self.language,
        )


class ReportMarkerRead(BaseModel):
    code: ReportMarkerCode
    detail: str | None = None


class ReportResultRead(BaseModel):
    """Angka beku satu produk untuk satu jenis skor. Skor 0 sampai 1, dua digit desimal."""

    model_config = ConfigDict(from_attributes=True)

    score_split: ScoreSplit
    cases_graded_count: int = Field(description="Kasus yang dinilai untuk produk ini")
    cases_basis_count: int = Field(description="Kasus yang menjadi dasar perbandingan")
    det_score: Decimal | None = Field(description="Skor jalur deterministik")
    llm_score: Decimal | None = Field(description="Skor penilai LLM, terpisah dari deterministik")
    total_score: Decimal | None = Field(description="Rata-rata kedua skor; dasar peringkat")
    failure_rate: Decimal | None = Field(
        description="Persen upaya gagal teknis (percobaan ulang ikut dihitung); kosong untuk manual"
    )
    rank: int | None = Field(description="Peringkat; kosong bila total skor kosong")
    repetitions: int
    product_model_version: str | None
    judge_model_version: str | None


class ReportSessionRead(BaseModel):
    session_id: uuid.UUID
    product_label: str
    measurement_kind: MeasurementKind
    is_trial: bool = Field(description="Sesi Uji Coba wajib diberi tanda yang jelas")
    markers: list[ReportMarkerRead]
    results: list[ReportResultRead]


class ReportRead(BaseModel):
    id: uuid.UUID
    title: str
    score_scope: ScoreScope
    has_dev_scores: bool
    measurement_filter: MeasurementFilter
    product_label_mode: ProductLabelMode
    language: ReportLanguage
    status: ReportStatus
    rubric_version_id: uuid.UUID
    judge_model_name: str
    case_basis_count: int = Field(description="Jumlah kasus yang dijawab semua produk")
    created_at: datetime
    sessions: list[ReportSessionRead]


class ReportCreateResponse(BaseModel):
    """201 bila laporan tersimpan. 200 dengan report kosong bila tidak ada kasus yang sama."""

    report: ReportRead | None = Field(
        description="Laporan yang tersimpan; kosong bila hanya ada peringatan"
    )
    warning: str | None = Field(
        default=None,
        description="NO_SHARED_CASES: tidak ada kasus yang sama, tidak ada angka perbandingan",
    )

    @classmethod
    def from_generated(cls, generated: GeneratedReport) -> "ReportCreateResponse":
        report = generated.report
        if report is None:
            return cls(report=None, warning=generated.warning)
        sessions = [
            ReportSessionRead(
                session_id=row.session_id,
                product_label=row.product_label,
                measurement_kind=row.measurement_kind,
                is_trial=row.is_trial,
                markers=[ReportMarkerRead(**marker) for marker in row.markers],
                results=[
                    ReportResultRead.model_validate(result)
                    for result in generated.results
                    if result.session_id == row.session_id
                ],
            )
            for row in generated.sessions
        ]
        return cls(
            report=ReportRead(
                id=report.id,
                title=report.title,
                score_scope=report.score_scope,
                has_dev_scores=report.has_dev_scores,
                measurement_filter=report.measurement_filter,
                product_label_mode=report.product_label_mode,
                language=report.language,
                status=report.status,
                rubric_version_id=report.rubric_version_id,
                judge_model_name=report.judge_model_name,
                case_basis_count=report.case_basis_count,
                created_at=report.created_at,
                sessions=sessions,
            ),
            warning=None,
        )


class ReportSessionNotFoundBody(ErrorBody):
    """404 REPORT_SESSION_NOT_FOUND."""

    missing: list[uuid.UUID] = Field(description="Id sesi yang tidak ditemukan")


class ReportRejectionRead(BaseModel):
    requirement: str = Field(description="RUBRIC_VERSION, JUDGE_MODEL, atau SUITE_VERSION")
    message: str


class ReportNotComparableBody(ErrorBody):
    """409 REPORT_NOT_COMPARABLE: semua alasan sekaligus, bukan hanya yang pertama."""

    selection_errors: list[str] = Field(
        description="NEED_AT_LEAST_TWO_PRODUCTS atau DUPLICATE_PRODUCT"
    )
    ineligible: dict[uuid.UUID, list[str]] = Field(
        description="Sesi yang belum layak dan kode alasannya, misalnya SESSION_CANCELLED"
    )
    rejections: list[ReportRejectionRead]


class EligibleSessionReasonRead(BaseModel):
    code: str = Field(description="Kode alasan, misalnya SESSION_NOT_GRADED atau SESSION_CANCELLED")
    message: str = Field(description="Pesan siap tampil untuk pengguna")


class EligibleSessionRead(BaseModel):
    """Satu sesi dan apakah ia layak jadi dasar laporan.

    eligible hanya soal sesi itu sendiri. Dua sesi layak masih bisa mendapat 409
    REPORT_NOT_COMPARABLE bila rubrik, penilai, atau suite-nya berbeda.
    """

    session_id: uuid.UUID
    label: str = Field(description="Nama sesi, untuk membedakan dua sesi dari produk yang sama")
    product_id: uuid.UUID = Field(
        description="Id produk; dua sesi dengan product_id sama tidak boleh dalam satu laporan"
    )
    product_name: str
    created_at: datetime | None = Field(description="Waktu sesi dibuat")
    session_type: SessionType
    purpose: SessionPurpose
    status: SessionStatus
    judge_model_version: str | None = Field(
        description="Versi model penilai yang menilai sesi; kosong bila belum dinilai"
    )
    eligible: bool
    reasons: list[EligibleSessionReasonRead] = Field(
        description="Semua alasan sesi belum layak; kosong bila eligible"
    )

    @classmethod
    def from_item(cls, item: EligibleSession) -> "EligibleSessionRead":
        return cls(
            session_id=item.session_id,
            label=item.label,
            product_id=item.product_id,
            product_name=item.product_name,
            created_at=item.created_at,
            session_type=item.session_type,
            purpose=item.purpose,
            status=item.status,
            judge_model_version=item.judge_model_version,
            eligible=item.eligible,
            reasons=[
                EligibleSessionReasonRead(code=r.code, message=r.message) for r in item.reasons
            ],
        )
