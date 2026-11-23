"""Bentuk request dan response modul reports.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

PBI-15 SCRUM-79 SCRUM-313: POST /reports dan GET /reports/eligible-sessions. Aturan isian ditulis di sini sekali; frontend tidak
menyalinnya.
PBI-15 SCRUM-79 SCRUM-362: bentuk POST /reports/validate (ReportValidateRequest, ReportValidationRead).

PBI-17 SCRUM-84 SCRUM-331: bentuk response GET /reports/trend (TrendRead).
PBI-17 SCRUM-84 SCRUM-332: POST /reports/{id}/publish dan GET /published-reports.
PBI-15 SCRUM-79 SCRUM-320: bentuk baca laporan tersimpan (GET /reports, /reports/{id}, PDF).
Isinya hasil beku dari tabel D1, jadi membuka ulang laporan dan membuat PDF tidak pernah
menghitung ulang. Bentuknya sengaja terpisah dari ReportRead milik POST: riwayat dibaca
semua peran dan dibagikan sebagai PDF, jadi tidak membawa id sesi/produk dan total_score
(review #145).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.auth.schemas import ErrorBody
from app.modules.cases.models import SplitTag
from app.modules.reports.eligible_sessions import EligibleSession
from app.modules.reports.models import (
    MeasurementFilter,
    MeasurementKind,
    ProductLabelMode,
    PublishedReport,
    Report,
    ReportLanguage,
    ReportMarkerCode,
    ReportStatus,
    ScoreScope,
    ScoreSplit,
)
from app.modules.reports.report_generation import GeneratedReport, ReportRequest
from app.modules.reports.report_validation import ReportValidation, ViolationKind
from app.modules.reports.trend import TrendMetric, TrendRange
from app.modules.reports.trend_service import ProductTrend
from app.modules.runs.models import (
    AttemptSource,
    AttemptStatus,
    SessionPurpose,
    SessionStatus,
    SessionType,
)
from app.shared.text import strip_blank

_TITLE_MAX = 200
_MESSAGE_DESCRIPTION = "Pesan siap tampil untuk pengguna"


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
    """Satu penanda D2 dari report_sessions.markers. Dipakai POST dan riwayat (SCRUM-320)."""

    model_config = ConfigDict(from_attributes=True)

    code: ReportMarkerCode
    detail: str | None = Field(default=None, description="Keterangan tambahan, misalnya jumlah")


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


class ReportValidateRequest(BaseModel):
    """Pilihan sesi yang diperiksa tanpa menyimpan laporan (dry-run)."""

    model_config = ConfigDict(extra="forbid")

    session_ids: list[uuid.UUID] = Field(
        description=(
            "Sesi yang dipilih sejauh ini. Boleh kosong atau satu sesi: itu dijawab sebagai "
            "pelanggaran NEED_AT_LEAST_TWO_PRODUCTS, bukan galat, karena halaman memanggil "
            "ini tiap pilihan berubah."
        ),
    )
    score_scope: ScoreScope = Field(
        default=ScoreScope.TEST,
        description="Jenis nilai yang ingin dibuat. dev dan dev_test hanya untuk Admin dan "
        "Developer AI; peran lain mendapat 403 REPORT_SCOPE_FORBIDDEN.",
    )
    measurement_filter: MeasurementFilter = Field(
        default=MeasurementFilter.ALL,
        description="Saring sesi otomatis atau manual saja; bawaannya semuanya.",
    )


class ReportViolationRead(BaseModel):
    kind: ViolationKind = Field(
        description="selection (kombinasi sesi), session (satu sesi tidak layak), "
        "requirement (rubrik, penilai, suite), atau case_slice (irisan kasus kosong)"
    )
    code: str = Field(description="Kode stabil, misalnya SESSION_CANCELLED atau RUBRIC_VERSION")
    message: str = Field(description=_MESSAGE_DESCRIPTION)
    session_id: uuid.UUID | None = Field(
        default=None, description="Sesi yang bermasalah; hanya terisi untuk kind session"
    )


class ReportWarningRead(BaseModel):
    code: str = Field(
        description="REPETITIONS_DIFFER, MANUAL_ANSWER_RANGE_OVER_LIMIT, MODEL_VERSION_DIFFERS, "
        "atau TRIAL_SESSION"
    )
    message: str = Field(description=_MESSAGE_DESCRIPTION)
    session_id: uuid.UUID | None = Field(
        default=None, description="Sesi yang ditandai; kosong bila berlaku untuk seluruh pilihan"
    )


class ReportValidationRead(BaseModel):
    """Hasil dry-run. Server tetap memeriksa ulang saat POST /reports (tidak percaya FE)."""

    valid: bool = Field(description="True bila tidak ada pelanggaran; peringatan tidak dihitung")
    violations: list[ReportViolationRead] = Field(
        description="Semua pelanggaran sekaligus; POST /reports akan menolak selama ada isinya"
    )
    warnings: list[ReportWarningRead] = Field(
        description="Penanda yang tidak memblokir; tetap tampil di laporan"
    )
    available_scopes: list[ScoreScope] = Field(
        description="Jenis nilai yang boleh dipilih peran ini, dihitung dari jenis kasus "
        "(dev, test) pada irisan kasus sesi yang layak. Kosong bila tidak ada irisan."
    )

    @classmethod
    def from_validation(cls, validation: ReportValidation) -> "ReportValidationRead":
        return cls(
            valid=validation.valid,
            violations=[
                ReportViolationRead(
                    kind=v.kind, code=v.code, message=v.message, session_id=v.session_id
                )
                for v in validation.violations
            ],
            warnings=[
                ReportWarningRead(code=w.code, message=w.message, session_id=w.session_id)
                for w in validation.warnings
            ],
            available_scopes=list(validation.available_scopes),
        )


class EligibleSessionReasonRead(BaseModel):
    code: str = Field(description="Kode alasan, misalnya SESSION_NOT_GRADED atau SESSION_CANCELLED")
    message: str = Field(description=_MESSAGE_DESCRIPTION)


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
    case_kinds: list[SplitTag] = Field(
        description="Jenis kasus di sesi ini: dev, test, atau keduanya; kosong bila tanpa kasus"
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
            case_kinds=list(item.case_kinds),
            eligible=item.eligible,
            reasons=[
                EligibleSessionReasonRead(code=r.code, message=r.message) for r in item.reasons
            ],
        )


# --- Riwayat laporan dan PDF (SCRUM-320) --------------------------------------


class ReportDetailResultRead(BaseModel):
    """Satu baris report_results di laporan tersimpan. Skor dua desimal, tanpa persen (AC6)."""

    model_config = ConfigDict(from_attributes=True)

    # Kunci penelusuran angka (PBI-16): menunjuk satu angka tanpa membuka sesi dan produknya.
    id: uuid.UUID = Field(description="Id angka ini untuk GET /reports/{id}/results/{result_id}")
    score_split: ScoreSplit
    cases_graded_count: int
    cases_basis_count: int
    det_score: Decimal | None
    llm_score: Decimal | None
    failure_rate: Decimal | None = Field(description="Persen dari total upaya; NULL untuk manual")
    # total_score tidak dikirim: D1 memakainya hanya untuk menghitung rank (review #145).
    rank: int | None
    repetitions: int
    product_model_version: str | None = Field(
        description="NULL di laporan mode kode, karena versi model biasanya menyebut produknya"
    )
    judge_model_version: str | None


class ReportDetailSessionRead(BaseModel):
    """Satu sesi (satu produk) di laporan tersimpan beserta hasil bekunya.

    Tanpa session_id dan product_id: di laporan mode kode (AC9) keduanya membuka produk mana
    yang ada di balik kode (review #145). Klien memakai product_label atau urutan daftar.
    """

    model_config = ConfigDict(from_attributes=True)

    product_label: str = Field(description="Nama asli atau kode, dipilih Admin saat membuat")
    measurement_kind: MeasurementKind
    # Uji Coba hanya disimpan di kolom ini, tidak diulang di markers (keputusan SCRUM-300).
    is_trial: bool = False
    markers: list[ReportMarkerRead] = Field(default_factory=list)
    results: list[ReportDetailResultRead]


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
    sessions: list[ReportDetailSessionRead]
    case_basis: list[ReportCaseBasisRead]


# --- Penelusuran angka laporan (PBI-16 SCRUM-326, D6) ----------------------------


class ReportCaseScoreRead(BaseModel):
    """Satu kasus dasar di balik satu angka: kode, versi, dan skornya, tanpa isi (AC1).

    Bentuknya sama untuk semua peran (D9: daftar kasus dan skor terbuka untuk semua).
    Yang berbeda hanya kode kasus test untuk Developer AI dan content_visible.
    """

    case_id: uuid.UUID
    case_code: str = Field(description="TERSEMBUNYI untuk kasus test bila dibaca Developer AI")
    case_version_no: int
    split_tag: SplitTag
    det_score: Decimal | None = Field(description="Rata-rata skor deterministik percobaan")
    llm_score: Decimal | None = Field(description="Rata-rata skor penilai LLM percobaan")
    content_visible: bool = Field(
        description="True bila peran ini boleh membuka isi kasus, jawaban, dan alasan (AC3)"
    )


class ReportResultCasesRead(BaseModel):
    """Daftar kasus di balik satu angka laporan (GET /reports/{id}/results/{result_id}/cases)."""

    report_id: uuid.UUID
    result_id: uuid.UUID
    product_label: str = Field(description="Label beku di laporan: nama asli atau kode")
    measurement_kind: MeasurementKind
    score_split: ScoreSplit
    rubric_version: str = Field(description="Versi rubrik run penilaian di balik angka ini")
    cases: list[ReportCaseScoreRead]


class ReportAttemptRead(BaseModel):
    """Satu percobaan di balik skor satu kasus (AC2): jawaban asli, skor, dan alasannya."""

    attempt_id: uuid.UUID
    repetition_no: int
    source: AttemptSource = Field(description="automatic atau manual; manual diberi label (AC2)")
    status: AttemptStatus
    graded: bool = Field(
        description="false untuk percobaan gagal teknis: tampilkan 'tidak dinilai' (AC2)"
    )
    det_score: Decimal | None
    llm_score: Decimal | None
    answer_text: str | None = Field(description="Jawaban asli produk AI")
    explanation: str | None = Field(description="Penjelasan penilai LLM")
    reasons: list[str] = Field(description="Kode alasan penilaian per jalur")
    criteria: list[dict[str, Any]] = Field(description="Hasil penilaian per kriteria rubrik")
    filled_by_name: str | None = Field(
        default=None, description="Siapa yang mengisi jawaban manual; null untuk otomatis"
    )
    answered_at: datetime | None = Field(
        default=None, description="Tanggal jawaban manual diperoleh dari platform"
    )


class ReportCaseDetailRead(BaseModel):
    """Satu kasus di balik satu angka, dibuka sampai jawaban asli (D6 langkah 5-7).

    Hanya dikirim ke peran yang boleh melihat isi kasus (AC3); peran lain mendapat 403.
    """

    report_id: uuid.UUID
    result_id: uuid.UUID
    case_id: uuid.UUID
    case_code: str
    case_version_no: int
    split_tag: SplitTag
    title: str
    category: str | None
    question: str | None = Field(description="Isi kasus dari snapshot sesi")
    product_label: str = Field(description="Label beku di laporan: nama asli atau kode")
    measurement_kind: MeasurementKind
    rubric_version: str = Field(description="Versi rubrik yang menilai percobaan-percobaan ini")
    det_score: Decimal | None
    llm_score: Decimal | None
    attempts: list[ReportAttemptRead]


class TrendPointRead(BaseModel):
    """Satu titik tren: sesi layak terbaru pada satu bulan. Tanpa id sesi."""

    month: date = Field(description="Hari pertama bulan titik ini (YYYY-MM-01)")
    label: str = Field(description="Nama sesi yang menjadi titik bulan ini")
    value: Decimal | None = Field(
        description="Skor 0 sampai 1, dua digit desimal; kosong bila tidak ada kasus yang sama "
        "atau skor jalur yang dipilih kosong"
    )
    breaks: list[str] = Field(
        description="Alasan garis diputus antara titik sebelumnya dan titik ini: RUBRIC_VERSION, "
        "JUDGE_MODEL, SUITE_VERSION (juga untuk versi kasus), atau MODEL_VERSION_DIFFERS. "
        "Kosong bila titik ini tersambung ke titik sebelumnya."
    )
    markers: list[str] = Field(
        description="Penanda titik: TRIAL_SESSION, MANUAL_ANSWER_RANGE_OVER_LIMIT, "
        "REPETITIONS_DIFFER (jumlah percobaan berbeda, hanya sesi manual)"
    )
    case_count: int = Field(
        description="Jumlah kasus yang sama pada segmen garis titik ini; semua titik di satu "
        "segmen memakai kumpulan kasus yang sama, jadi nilainya sama. 0 bila tidak ada kasus "
        "yang sama"
    )


class TrendRead(BaseModel):
    """Tren satu metrik untuk satu produk. Skor dev hanya ada bila peran berhak memintanya."""

    product_id: uuid.UUID
    product_name: str
    score_scope: ScoreScope = Field(description="test, dev, atau dev_test (All)")
    metric: TrendMetric
    range: TrendRange
    points: list[TrendPointRead] = Field(description="Urut dari bulan paling awal")
    warnings: list[str] = Field(
        description="NO_SHARED_CASES: ada garis tanpa kasus yang sama, nilainya tidak digambar"
    )

    @classmethod
    def from_trend(cls, result: ProductTrend) -> "TrendRead":
        return cls(
            product_id=result.product_id,
            product_name=result.product_name,
            score_scope=result.scope,
            metric=result.metric,
            range=result.window,
            points=[
                TrendPointRead(
                    month=point.month,
                    label=point.label,
                    value=point.value,
                    breaks=list(point.breaks),
                    markers=list(point.markers),
                    case_count=point.case_count,
                )
                for point in result.trend.points
            ],
            warnings=list(result.trend.warnings),
        )


class PublishReport(BaseModel):
    """Badan POST /reports/{id}/publish. version_no diisi server, bukan klien."""

    model_config = ConfigDict(extra="forbid")

    supersedes_id: uuid.UUID | None = None


class PublicationRead(BaseModel):
    """Versi terbit yang baru dibuat."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    version_no: int
    supersedes_id: uuid.UUID | None


class PublishedReportListItem(BaseModel):
    """Satu baris GET /published-reports. Nama produk adalah label beku."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    version_no: int
    title: str
    published_at: datetime
    published_by: uuid.UUID
    product_labels: list[str]

    @classmethod
    def from_current(
        cls, publication: PublishedReport, report: Report, product_labels: list[str]
    ) -> "PublishedReportListItem":
        return cls(
            id=publication.id,
            report_id=publication.report_id,
            version_no=publication.version_no,
            title=report.title,
            published_at=publication.published_at,
            published_by=publication.published_by,
            product_labels=product_labels,
        )
