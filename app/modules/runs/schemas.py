"""Bentuk request dan response modul runs.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

TODO(PBI-11, PBI-12, PBI-13): definisikan schema sesuai kontrak.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from app.modules.cases.models import SplitTag

# Delapan bagian kasus yang sama dengan compare versi, supaya FE memakai satu tipe.
from app.modules.cases.schemas import VersionSections

# Satu enum status sesi untuk tabel (#89) dan kontrak API, supaya keduanya tidak menyimpang.
from app.modules.runs.models import (
    AttemptSource,
    AttemptStatus,
    SessionStatus,
    StopReason,
)
from app.shared.text import strip_blank

# PBI-11 AC3: satu angka pengulangan untuk seluruh pengukuran.
MIN_REPEATS = 1
MAX_REPEATS = 5
DEFAULT_REPEATS = 3
# PBI-22 AC1: sesi manual standar 1 percobaan (beban awal 2 platform x 100 kasus x 1).
MANUAL_DEFAULT_REPEATS = 1


def _as_utc(value: datetime) -> datetime:
    """Waktu dari database tanpa zona (SQLite) dibaca sebagai UTC, supaya selalu bertanda zona."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Waktu keluar sebagai ISO 8601 UTC; tampilan WIB diatur FE.
UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]


class CaseFilter(StrEnum):
    """Pilihan kasus yang ikut diukur (PBI-11 AC2). both berarti dev dan test."""

    DEV = "dev"
    TEST = "test"
    BOTH = "both"


class MeasurementPurpose(StrEnum):
    """Pengukuran Resmi atau Uji Coba (PBI-11 AC1, D2 measurement_sessions.purpose)."""

    OFFICIAL = "official"
    TRIAL = "trial"


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


class ProgressResponse(BaseModel):
    """Angka layar progres yang di-poll tiap beberapa detik (PBI-12 AC5, AC6).

    Tidak memuat jumlah coba ulang yang sedang berjalan dan tidak memuat
    penanda produk bermasalah (AC5). Uang dikirim sebagai string.
    """

    completed_calls: int = Field(description="Panggilan selesai, termasuk yang gagal")
    total_calls: int = Field(description="Kasus kali pengulangan")
    percent_complete: Decimal = Field(description="Persen selesai, satu desimal")
    failed_calls: int = Field(description="Panggilan selesai yang gagal")
    percent_failed: Decimal = Field(
        description="Persen gagal dari panggilan yang sudah selesai, satu desimal"
    )
    remaining_seconds: int | None = Field(
        description="Perkiraan sisa waktu dalam detik, null sebelum ada panggilan selesai"
    )
    running_cost_idr: Decimal = Field(description="Biaya berjalan dalam rupiah")
    remaining_budget_idr: Decimal = Field(description="Sisa anggaran bulan ini untuk produk")
    started_at: datetime | None = Field(
        description="Waktu sesi mulai berjalan, null selama sesi masih antre"
    )
    updated_at: datetime = Field(description="Waktu angka ini terakhir diperbarui")


class SessionProgressRead(BaseModel):
    """Progres "X dari N" di daftar sesi (PBI-11 AC10)."""

    completed: int = Field(
        description="Percobaan yang sudah punya hasil akhir: berhasil atau gagal teknis"
    )
    total: int = Field(description="Semua percobaan sesi, kasus kali pengulangan. 0 selama antre")


class SessionListItem(BaseModel):
    """Satu baris daftar sesi pengukuran yang belum diarsipkan (PBI-11 AC10)."""

    id: uuid.UUID
    label: str
    created_at: datetime
    created_by: uuid.UUID
    created_by_name: str | None = Field(
        description="Nama pembuat sesi, null bila akunnya tidak ada"
    )
    product_id: uuid.UUID
    product_name: str | None = Field(
        description="Nama produk AI, tetap terisi walau produknya sudah dihapus"
    )
    case_count: int | None = Field(
        description="Kasus di snapshot sesi. null selama sesi antre, snapshot dibuat saat mulai"
    )
    repetitions: int
    status: SessionStatus
    progress: SessionProgressRead


class SessionArchiveResponse(BaseModel):
    """Hasil arsip sesi. Sesinya tidak lagi tampil di GET /measurements (PBI-11 AC10)."""

    id: uuid.UUID
    archived_at: datetime
    archived_by: uuid.UUID = Field(description="Pengguna yang mengarsipkan sesi")


class SessionAcceptRequest(BaseModel):
    """Terima apa adanya (PBI-12 AC9). Alasan wajib; dicek di server supaya kodenya REASON_REQUIRED."""

    reason: str | None = Field(
        default=None,
        description="Alasan tertulis Admin menerima sesi walau ada kegagalan teknis",
    )


class SessionAcceptResponse(BaseModel):
    """Tanda terima apa adanya sesudah PATCH (terisi) atau DELETE (kosong)."""

    id: uuid.UUID
    accepted_as_is_at: datetime | None
    accepted_as_is_by: uuid.UUID | None = Field(description="Admin yang menerima sesi")
    accepted_as_is_reason: str | None


class SessionRetryRequest(BaseModel):
    """Coba ulang yang gagal (PBI-12 AC3): per kasus lewat attempt_ids, atau semua (D4)."""

    attempt_ids: list[uuid.UUID] | None = Field(
        default=None, description="Percobaan gagal yang dicentang. Kosongkan bila all_failed"
    )
    all_failed: bool = Field(
        default=False, description="true untuk mencoba ulang semua percobaan yang gagal"
    )

    @model_validator(mode="after")
    def _tepat_satu_pilihan(self) -> "SessionRetryRequest":
        if bool(self.attempt_ids) == self.all_failed:
            raise ValueError("Pilih attempt_ids atau all_failed, tepat salah satu.")
        return self


class SessionRetryResponse(BaseModel):
    """Sesi berjalan lagi; runner hanya memanggil ulang percobaan yang dibuka."""

    id: uuid.UUID
    status: SessionStatus
    retried_attempt_ids: list[uuid.UUID] = Field(
        description="Percobaan yang kembali pending, urut kasus lalu nomor pengulangan"
    )


class CallAttemptLog(BaseModel):
    """Satu upaya panggilan di call_logs, untuk tombol "lihat detail teknis" (D5 langkah 33-38)."""

    try_no: int = Field(description="Upaya ke-n pada percobaan ini, 1 = panggilan awal")
    is_manual_retry: bool = Field(description="true untuk upaya dari Coba ulang atau Lanjutkan")
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    result: str = Field(description="ok, atau jenis kegagalan teknis")
    failure_kind: str | None
    model_version: str
    error_summary: str | None = Field(
        description="Ringkasan galat dari worker. null bila berhasil atau disamarkan"
    )
    raw_response: dict | None = Field(
        description="Respons asli penyedia sebagai bukti. null bila disamarkan"
    )


class AttemptCalls(BaseModel):
    """Riwayat upaya satu percobaan, urut try_no (PBI-12 AC3)."""

    attempt_id: uuid.UUID
    repetition_no: int
    status: AttemptStatus
    masked: bool = Field(
        description=(
            "true bila Developer AI membuka kasus selain dev (apa pun status sesinya): "
            "hanya jenis kegagalan dan waktu"
        )
    )
    calls: list[CallAttemptLog]


class AttemptResult(BaseModel):
    """Satu percobaan: jawaban mentah apa adanya dari produk (PBI-11 AC8)."""

    id: uuid.UUID = Field(
        description=(
            "id percobaan untuk coba ulang per kasus (attempt_ids) dan detail teknis. "
            "Tetap ada di kasus yang disamarkan karena tidak memuat isi kasus"
        )
    )
    repetition_no: int
    status: AttemptStatus
    answer_text: str | None = Field(
        description=(
            "Jawaban mentah. null bila belum ada, gagal teknis, atau disamarkan. Developer AI "
            "menerima jawaban kasus dev segera setelah percobaannya selesai, tanpa menunggu sesi berakhir"
        )
    )
    model_version: str | None
    started_at: UtcDateTime | None = Field(
        description=(
            "Waktu worker mulai memanggil produk (ISO 8601 UTC) pada upaya terakhir. "
            "null untuk data lama dan percobaan yang belum jalan"
        )
    )
    finished_at: UtcDateTime | None = Field(
        description=(
            "Waktu respons diterima atau gagal (ISO 8601 UTC) pada upaya terakhir. "
            "null bila belum selesai, setelah coba ulang dibuka, atau data lama"
        )
    )


class SessionCaseResult(BaseModel):
    """Satu kasus di snapshot sesi beserta percobaannya, urut nomor pengulangan."""

    snapshot_item_id: uuid.UUID
    case_id: uuid.UUID
    case_code: str
    title: str
    question: str | None = Field(description="Pertanyaan kasus. null bila disamarkan")
    split_tag: SplitTag | None
    masked: bool = Field(
        description="true untuk kasus test yang dibuka Developer AI: isi dan jawaban disamarkan"
    )
    version_no: int | None = Field(description="Versi kasus yang dibekukan. null bila disamarkan")
    sections: VersionSections | None = Field(
        description="Delapan bagian kasus dari snapshot (AC7). null bila disamarkan"
    )
    attempts: list[AttemptResult]


class SessionDetail(SessionListItem):
    """Detail sesi: kolom daftar ditambah hasil per kasus (PBI-11 AC8)."""

    purpose: MeasurementPurpose
    # Nilai sama dengan case_filter di form Mulai, jadi FE memakai satu tipe.
    split_filter: CaseFilter
    snapshot_created_at: datetime | None = Field(
        description="Waktu snapshot kasus dibuat (AC6). null selama sesi antre"
    )
    started_at: datetime | None
    finished_at: datetime | None
    archived_at: datetime | None = Field(description="Terisi bila sesi sudah diarsipkan")
    # PBI-12 AC8, AC9 (SCRUM-214): dibaca ulang supaya FE bisa memulihkan kendali
    # setelah muat ulang atau saat Admin lain membuka sesi.
    stopped_at: datetime | None = Field(
        description="Waktu sesi berhenti (jeda, anggaran, restart, error). null selama berjalan"
    )
    stop_reason: StopReason | None = Field(
        description="Penyebab berhenti: user, budget, restart; error untuk persiapan gagal atau versi berubah setelah resume"
    )
    accepted_as_is_at: datetime | None = Field(
        description="Waktu Admin menandai terima apa adanya. null bila tidak tertanda"
    )
    accepted_as_is_by: uuid.UUID | None = Field(description="Admin yang menerima sesi")
    accepted_as_is_reason: str | None = Field(description="Alasan tertulis terima apa adanya")
    resumed_after_long_pause: bool = Field(
        default=False, description="Pernah dilanjutkan setelah jeda melebihi batas konfigurasi"
    )
    model_version_unverified: bool = Field(
        default=False, description="Pernah dilanjutkan tanpa bukti versi terverifikasi"
    )
    resume_error_code: Literal["MODEL_VERSION_CHANGED"] | None = None
    report_eligible: bool = Field(
        description=(
            "Boleh jadi dasar laporan (D4): finished, atau finished_with_failures yang "
            "diterima apa adanya. cancelled dan status lain false (PBI-15/16)"
        )
    )
    cases: list[SessionCaseResult] = Field(
        description="Kosong selama sesi antre, karena snapshot dibuat saat sesi mulai"
    )


# D2 measurement_sessions.label varchar(200).
_PANJANG_LABEL = 200


class StartRequest(SummaryRequest):
    """Body POST /measurements (PBI-11 AC1, SCRUM-207).

    Isian ringkasan diwarisi supaya Mulai divalidasi dengan aturan yang sama
    seperti Lihat ringkasan, ditambah label dan tujuan yang wajib.
    """

    label: str = Field(
        min_length=1, max_length=_PANJANG_LABEL, description="Nama sesi, wajib, maksimal 200"
    )
    purpose: MeasurementPurpose = Field(description="official = Pengukuran Resmi, trial = Uji Coba")
    # AC1, D3 langkah 19. Opsional sampai form punya dropdown penilai (keputusan PO K2, 7 Nov);
    # kalau diisi, StartSessionService memeriksa penilai ada dan aktif (SCRUM-258).
    judge_provider_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Provider penilai (LLM judge) dari GET /judge-providers. Boleh kosong; penilai yang "
            "tidak dikenal atau nonaktif ditolak 422 JUDGE_PROVIDER_NOT_AVAILABLE"
        ),
    )

    @field_validator("label", mode="before")
    @classmethod
    def buang_spasi_tepi(cls, value):
        # Label berisi karakter kosong saja (termasuk zero-width, SCRUM-214) menjadi kosong,
        # lalu ditolak min_length.
        return strip_blank(value) if isinstance(value, str) else value


class StartResponse(BaseModel):
    """Jawaban 201 tombol Mulai (D3 langkah 23)."""

    session_id: uuid.UUID
    status: SessionStatus = Field(description="Selalu queued: worker yang menjalankan saat giliran")
    # Nama D2 (keputusan PO di #89): antrean FIFO diurutkan dengan queued_at.
    queued_at: datetime = Field(description="Waktu masuk antrean; yang lebih awal jalan lebih dulu")


class ManualStartRequest(StartRequest):
    """Body POST /manual-measurements (PBI-22 AC1, SCRUM-230).

    Isian sesi otomatis diwarisi (suite, filter, label, tujuan, penilai), supaya aturan
    validasinya satu. Bedanya: produk harus manual, dan percobaan standar 1, bukan 3.
    """

    product_id: uuid.UUID = Field(description="Satu produk AI manual yang dicatat tim legal")
    repeat_count: int = Field(
        default=MANUAL_DEFAULT_REPEATS,
        ge=MIN_REPEATS,
        le=MAX_REPEATS,
        description="Percobaan per kasus, standar 1, maksimum 5, ditentukan saat sesi dibuat",
    )

    @field_validator("repeat_count", mode="before")
    @classmethod
    def repeat_null_memakai_bawaan(cls, value):
        # Nama sama dengan validator induk supaya menggantikannya, bukan menambah.
        return MANUAL_DEFAULT_REPEATS if value is None else value


class ManualStartResponse(BaseModel):
    """Jawaban 201 POST /manual-measurements: sesi siap diisi (D8 Belum terisi)."""

    session_id: uuid.UUID
    status: SessionStatus = Field(description="Selalu not_filled: tanpa antrean (PBI-22 AC10)")
    case_count: int = Field(description="Kasus yang dibekukan di snapshot")
    repetitions: int = Field(description="Percobaan per kasus")
    total_answers: int = Field(description="N pada progres 'X dari N jawaban terisi'")


class ManualAttemptSlot(BaseModel):
    """Satu kotak jawaban (Percobaan n) di kartu kasus sesi manual (PBI-22 AC4)."""

    attempt_id: uuid.UUID
    repetition_no: int
    filled: bool = Field(description="Teks jawaban atau 'Platform menolak menjawab' tersimpan")
    answered_at_platform: datetime | None = Field(
        description="Kapan jawaban diperoleh dari platform (AC7)"
    )


class ManualCaseProgress(BaseModel):
    """Satu kartu kasus: pertanyaan untuk disalin (AC3) dan slot per percobaan (AC4)."""

    snapshot_item_id: uuid.UUID
    case_id: uuid.UUID
    case_code: str
    title: str
    question: str
    split_tag: str
    filled: bool = Field(description="Semua percobaan kasus ini sudah terisi (AC8)")
    attempts: list[ManualAttemptSlot]


class ManualSessionDetail(BaseModel):
    """Halaman sesi manual (PBI-22 AC8, AC11-AC13). Tanpa biaya dan anggaran (AC10)."""

    id: uuid.UUID
    label: str
    purpose: MeasurementPurpose
    status: SessionStatus
    product_name: str
    vendor_name: str | None
    site_url: str | None
    manual_params: str | None = Field(description="Paket produk manual saat sesi dibuat (PBI-23)")
    case_filter: CaseFilter
    repetitions: int = Field(description="Percobaan per kasus (AC12, penanda PBI-15)")
    filled_answers: int = Field(description="X pada 'X dari N jawaban terisi', seluruh sesi")
    total_answers: int = Field(description="N: kasus x percobaan")
    answer_date_from: datetime | None
    answer_date_to: datetime | None
    date_range_warning: bool = Field(
        description="Rentang tanggal jawaban melebihi batas hari (AC13; batas TBA, bawaan mati)"
    )
    created_by: uuid.UUID
    created_at: datetime
    finalized_at: datetime | None
    finalized_by: uuid.UUID | None
    cases: list[ManualCaseProgress] = Field(
        description="Kartu kasus urut kode; disaring bila unfilled=true"
    )


class ManualFinalizeResponse(BaseModel):
    """Jawaban Tandai selesai sesi manual (PBI-22 AC8, D9 finalized_at/finalized_by)."""

    id: uuid.UUID
    status: SessionStatus = Field(description="Selalu finalized: final, tidak dapat dibuka kembali")
    finalized_at: datetime
    finalized_by: uuid.UUID


class SessionControlResponse(BaseModel):
    """Kontrol diterima (202); call aktif masih boleh menyelesaikan persistence."""

    id: uuid.UUID
    status: SessionStatus
    stopped_at: datetime
    stop_reason: StopReason


class SessionResumeRequest(BaseModel):
    """Konfirmasi eksplisit Admin bila pernah sukses tetapi provider belum memberi versi."""

    confirm_unverified_version: bool = Field(default=False, strict=True)


class SessionResumeResponse(BaseModel):
    """202: worker yang sama melanjutkan pending; cancellation sebelum mulai masuk queued."""

    id: uuid.UUID
    status: SessionStatus
    resumed_after_long_pause: bool
    model_version_unverified: bool


# PBI-14 SCRUM-226: Penilaian, pemicu manual, penanganan gangguan teknis & visibilitas hasil.


class ScorePair(BaseModel):
    """Skor deterministik, kualitas rubrik, dan format penilai LLM (PBI-14, SCRUM-225)."""

    deterministic: Decimal | None = Field(
        default=None, description="Skor jalur deterministik (proporsi sitasi 0.00-1.00)"
    )
    quality: Decimal | None = Field(
        default=None,
        description="Skor kualitas substansi hukum berdasarkan kriteria rubrik (skala 0.00-1.00)",
    )
    format: Decimal | None = Field(
        default=None, description="Skor kepatuhan format dan struktur jawaban (skala 0.00-1.00)"
    )


class ThreeScores(BaseModel):
    """Tiga skor terpisah yang diminta PBI-11 AC9: dev, test, dan gabungan."""

    dev: ScorePair = Field(description="Skor kasus dev")
    test: ScorePair = Field(description="Skor kasus test (agregat)")
    combined: ScorePair = Field(description="Skor gabungan seluruh kasus")


class CategoryScore(BaseModel):
    """Pecahan skor per kategori kasus (PBI-14 AC10)."""

    category: str = Field(description="Nama kategori, atau 'Lain-lain' bila kosong")
    case_count: int = Field(description="Jumlah kasus dalam kategori ini")
    scores: ScorePair = Field(description="Rata-rata skor kategori")


class GradeSessionRequest(BaseModel):
    """Pemicu penilaian manual oleh Admin (PBI-14 AC6, AC7)."""

    rubric_version: str | None = Field(
        default=None,
        description="Versi rubrik yang dipilih Admin. Bila kosong, memakai versi aktif (default 'v1').",
    )


class AttemptGradeResult(BaseModel):
    """Hasil penilaian satu percobaan."""

    attempt_id: uuid.UUID = Field(description="ID percobaan")
    repetition_no: int = Field(description="Nomor pengulangan (1-5)")
    status: AttemptStatus = Field(description="Status percobaan")
    source: AttemptSource = Field(
        default=AttemptSource.AUTOMATIC,
        description="Asal percobaan: automatic atau manual (PBI-12)",
    )
    is_technical_disruption: bool = Field(
        description="True bila jawaban kosong, AI menolak, atau judge error (AC4)"
    )
    score: ScorePair | None = Field(
        default=None,
        description="Skor percobaan desimal 2 digit. null bila gangguan teknis atau disamarkan",
    )
    answer_text: str | None = Field(
        default=None,
        description="Jawaban mentah. null bila disamarkan untuk Developer AI pada kasus test",
    )
    explanation: str | None = Field(
        default=None,
        description="Penjelasan evaluasi penilai LLM (llm_judge_results.explanation)",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Daftar kode alasan standar per jalur (grading_result_reasons)",
    )
    criteria: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Hasil penilaian per kriteria (llm_criterion_results)",
    )


class CaseGradeResult(BaseModel):
    """Hasil penilaian satu kasus per baris tabel (AC8)."""

    snapshot_item_id: uuid.UUID
    case_id: uuid.UUID
    case_code: str
    title: str
    split_tag: SplitTag | None = None
    category: str | None = None
    question: str | None = Field(
        default=None, description="null bila disamarkan untuk Developer AI pada kasus test"
    )
    masked: bool = Field(
        default=False, description="true untuk kasus test yang dilihat Developer AI"
    )
    scores: ScorePair | None = Field(
        default=None,
        description="Rata-rata skor percobaan kasus. null bila disamarkan untuk Developer AI pada kasus test",
    )
    attempts: list[AttemptGradeResult] = Field(default_factory=list)


class SessionGradingResponse(BaseModel):
    """Hasil penilaian sesi pengukuran (PBI-14 AC3, AC6, AC7, AC8)."""

    grading_run_id: uuid.UUID
    session_id: uuid.UUID
    session_label: str | None = Field(default=None, description="Label sesi pengukuran yang diuji")
    product_name: str | None = Field(default=None, description="Nama produk AI yang diuji")
    rubric_version: str = Field(description="Versi rubrik yang digunakan (mis. 'v1')")
    kind: Literal["initial", "regrade"] = Field(
        description="initial untuk penilaian pertama, regrade untuk nilai ulang"
    )
    status: str = Field(description="completed bila sudah selesai dinilai")
    triggered_by: uuid.UUID = Field(description="Admin yang memicu penilaian")
    triggered_at: datetime = Field(description="Waktu pemicuan penilaian")
    is_latest: bool = Field(
        default=True, description="True bila merupakan hasil penilaian terbaru untuk sesi ini"
    )
    scores: ThreeScores = Field(description="Tiga skor terpisah: dev, test, dan gabungan")
    category_scores: list[CategoryScore] = Field(
        default_factory=list, description="Pecahan skor per kategori kasus"
    )
    cases: list[CaseGradeResult] = Field(
        default_factory=list, description="Daftar kasus beserta hasil penilaian per percobaan"
    )


class GradedSessionListItem(BaseModel):
    """Ringkasan sesi pengukuran selesai yang telah dinilai untuk Admin dan Viewer (PBI-14, SCRUM-226, SCRUM-227)."""

    session_id: uuid.UUID = Field(description="ID sesi pengukuran")
    label: str = Field(description="Label sesi pengukuran")
    product_name: str = Field(description="Nama produk AI yang diuji")
    finished_at: datetime | None = Field(
        default=None, description="Waktu sesi selesai (null bila belum tercatat)"
    )
    rubric_version: str | None = Field(
        default=None, description="Versi rubrik yang digunakan pada penilaian terbaru (mis. 'v1')"
    )
