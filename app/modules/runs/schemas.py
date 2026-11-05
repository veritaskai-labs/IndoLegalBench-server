"""Bentuk request dan response modul runs.

Schema di sini yang menjadi sumber kontrak OpenAPI. Kalau file ini
berubah, kontrak API ikut berubah, jadi wajib diumumkan ke tim.

TODO(PBI-11, PBI-12, PBI-13): definisikan schema sesuai kontrak.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.modules.cases.models import SplitTag

# Delapan bagian kasus yang sama dengan compare versi, supaya FE memakai satu tipe.
from app.modules.cases.schemas import VersionSections

# Satu enum status sesi untuk tabel (#89) dan kontrak API, supaya keduanya tidak menyimpang.
from app.modules.runs.models import AttemptStatus, SessionStatus, StopReason
from app.shared.text import strip_blank

# PBI-11 AC3: satu angka pengulangan untuk seluruh pengukuran.
MIN_REPEATS = 1
MAX_REPEATS = 5
DEFAULT_REPEATS = 3


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


class AttemptResult(BaseModel):
    """Satu percobaan: jawaban mentah apa adanya dari produk (PBI-11 AC8)."""

    repetition_no: int
    status: AttemptStatus
    answer_text: str | None = Field(
        description=(
            "Jawaban mentah. null bila belum ada, gagal teknis, disamarkan, atau sesi "
            "belum berakhir untuk Developer AI"
        )
    )
    model_version: str | None


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
    # AC1, D3 langkah 19. Opsional dan belum divalidasi: tabel judge_providers datang di PBI-14.
    judge_provider_id: uuid.UUID | None = Field(
        default=None, description="Provider penilai (LLM judge) yang dipilih; kosong sampai PBI-14"
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
