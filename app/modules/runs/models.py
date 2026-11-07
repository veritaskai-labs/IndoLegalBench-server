"""Tabel database milik modul runs.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/runs/.

Semua model wajib mewarisi Base dari app.shared.database supaya
terdeteksi Alembic.

PBI-11 SCRUM-76 (SCRUM-205), D2. Tabelnya dibuat migrasi 16014b26f0bf, yang
juga memasang trigger PostgreSQL (sesi tidak bisa dihapus, pilihan suite,
salinan produk, dan snapshot hanya
INSERT, jawaban percobaan permanen). Model di sini harus sama dengan migrasi
itu; tests/integration/test_measurement_models_pg.py memeriksanya.

CallLog (SCRUM-209) dibuat migrasi d3c8a1e54b20.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.text import sql_not_blank

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")
_USER_FK = "users.id"
_SESSION_FK = "measurement_sessions.id"


def _enum_values(members) -> list[str]:
    return [member.value for member in members]


class SessionPurpose(StrEnum):
    """Pengukuran Resmi atau Uji Coba (AC1)."""

    OFFICIAL = "official"
    TRIAL = "trial"


class SessionType(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class SplitFilter(StrEnum):
    DEV = "dev"
    TEST = "test"
    BOTH = "both"


class SessionStatus(StrEnum):
    """Status sesi per D4 (otomatis) dan D8 (manual). Perpindahan sah di session_transitions.

    paused mencakup Jeda dari pengguna dan sesi yang dihentikan sistem (restart, deploy,
    anggaran habis); D4 tidak memisahkan Dihentikan dari Dijeda.

    Lima status terakhir milik sesi manual (PBI-22 SCRUM-230, D9 menaruhnya di kolom yang
    sama): Belum terisi, Terisi sebagian, Lengkap, Dinilai, Selesai.
    """

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FINISHED = "finished"
    FINISHED_WITH_FAILURES = "finished_with_failures"
    FAILED = "failed"
    NOT_FILLED = "not_filled"
    PARTIALLY_FILLED = "partially_filled"
    COMPLETE = "complete"
    GRADED = "graded"
    FINALIZED = "finalized"


class StopReason(StrEnum):
    """paused: user, budget, restart, atau error versi setelah resume; failed: error persiapan."""

    USER = "user"
    BUDGET = "budget"
    RESTART = "restart"
    ERROR = "error"


class AttemptSource(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class EvidenceKind(StrEnum):
    """Jenis bukti jawaban manual (D9 attempt_evidence.kind). PBI-22 AC7, SCRUM-233."""

    FILE = "file"
    LINK = "link"


# D9: tipe berkas bukti yang diizinkan. Dibaca dari isi berkas (manual_evidence_rules).
EVIDENCE_MIME_TYPES = ("image/png", "image/jpeg", "application/pdf")


class AttemptStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    TECHNICAL_FAILURE = "technical_failure"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


# Alasan "terima apa adanya" wajib berisi teks nyata (AC9); kosong tak terlihat ditolak.
_REASON_NOT_BLANK = sql_not_blank("coalesce(accepted_as_is_reason, '')")

_RUNNING_AUTOMATIC = text("status = 'running' AND session_type = 'automatic'")


class MeasurementSession(Base):
    """Satu sesi = satu produk AI (AC1). Tidak pernah dihapus, hanya diarsipkan (AC10)."""

    __tablename__ = "measurement_sessions"
    __table_args__ = (
        CheckConstraint("repetitions BETWEEN 1 AND 5", name="ck_measurement_sessions_repetitions"),
        # Bukan trim() biasa: tab, NBSP, dan zero-width saja juga kosong (SCRUM-214, #89).
        CheckConstraint(sql_not_blank("label"), name="ck_measurement_sessions_label_not_blank"),
        CheckConstraint(
            "(archived_at IS NULL) = (archived_by IS NULL)",
            name="ck_measurement_sessions_archived_complete",
        ),
        CheckConstraint(
            "(accepted_as_is_at IS NULL AND accepted_as_is_by IS NULL "
            "AND accepted_as_is_reason IS NULL) OR "
            "(accepted_as_is_at IS NOT NULL AND accepted_as_is_by IS NOT NULL "
            f"AND {_REASON_NOT_BLANK})",
            name="ck_measurement_sessions_accepted_as_is_complete",
        ),
        # Baris arsip disembunyikan SCRUM-210, jadi tidak boleh memegang slot runner.
        CheckConstraint(
            "NOT (status = 'running' AND archived_at IS NOT NULL)",
            name="ck_measurement_sessions_running_not_archived",
        ),
        # Waktu berhenti tanpa penyebab (atau sebaliknya) tidak bisa dijelaskan ke Admin.
        CheckConstraint(
            "(stopped_at IS NULL) = (stop_reason IS NULL)",
            name="ck_measurement_sessions_stop_complete",
        ),
        # D9: Selesai (PBI-22 AC8) selalu tercatat bersama pelakunya.
        CheckConstraint(
            "(finalized_at IS NULL) = (finalized_by IS NULL)",
            name="ck_measurement_sessions_finalized_complete",
        ),
        Index("ix_measurement_sessions_product_id", "product_id"),
        # Satu sesi otomatis berjalan (AC3). Parsial di kedua dialek: tanpa
        # sqlite_where, SQLite membuatnya UNIQUE penuh pada session_type.
        Index(
            "uq_measurement_sessions_one_running_automatic",
            "session_type",
            unique=True,
            postgresql_where=_RUNNING_AUTOMATIC,
            sqlite_where=_RUNNING_AUTOMATIC,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[SessionPurpose] = mapped_column(
        _enum(SessionPurpose, "measurement_purpose_enum"), nullable=False
    )
    session_type: Mapped[SessionType] = mapped_column(
        _enum(SessionType, "measurement_session_type_enum"), nullable=False
    )
    # FK lewat nama tabel, bukan import model providers. Batas modul tetap utuh.
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_products.id"), nullable=False
    )
    repetitions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    split_filter: Mapped[SplitFilter] = mapped_column(
        _enum(SplitFilter, "measurement_split_filter_enum"), nullable=False
    )
    # Penilai LLM (AC1), dipilih saat membuat sesi. Boleh kosong sampai form punya
    # dropdown penilai (keputusan PO 7 Nov, SCRUM-258).
    judge_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "judge_providers.id",
            name="fk_measurement_sessions_judge_provider",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "measurement_session_status_enum"),
        nullable=False,
        default=SessionStatus.QUEUED,
        server_default=SessionStatus.QUEUED.value,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Urutan antrean FIFO (D2). Diisi saat sesi masuk antrean (SCRUM-207).
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # D2: kapan dan mengapa sesi berhenti (SCRUM-214). Kosong lagi saat sesi berjalan kembali.
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[StopReason | None] = mapped_column(
        _enum(StopReason, "measurement_stop_reason_enum"), nullable=True
    )
    resumed_after_long_pause: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    model_version_unverified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    resume_version_check_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    resume_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=True
    )
    accepted_as_is_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_as_is_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=True
    )
    accepted_as_is_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D9, sesi manual (PBI-22 SCRUM-230). Rentang tanggal jawaban dari platform untuk
    # peringatan rentang (AC13) dan laporan PBI-15; diperbarui setiap jawaban disimpan.
    answer_date_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answer_date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=True
    )


class MeasurementSessionSuite(Base):
    """Suite yang dipilih untuk sesi (AC2). Kasus dipilih per suite, bukan per kasus."""

    __tablename__ = "measurement_session_suites"
    __table_args__ = (Index("ix_measurement_session_suites_suite_id", "suite_id"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_SESSION_FK), primary_key=True
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suites.id"), primary_key=True
    )


class SessionProductCopy(Base):
    """Data produk saat sesi dibuat (AC6). Kredensial sengaja tidak disalin."""

    __tablename__ = "session_product_copies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_SESSION_FK), nullable=False, unique=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_products.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Tipe enum milik ai_products (PBI-10, PBI-23), ditulis ulang supaya tidak
    # mengimpor model modul lain. Nilainya harus sama dengan providers.models.
    measurement_type: Mapped[str] = mapped_column(
        Enum("automatic", "manual", name="ai_product_measurement_type_enum"), nullable=False
    )
    provider_type: Mapped[str | None] = mapped_column(
        Enum(
            "openai_compatible",
            "gemini_interactions",
            "anthropic_messages",
            name="ai_product_provider_type_enum",
        ),
        nullable=True,
    )
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_params: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    model_version_at_start: Mapped[str | None] = mapped_column(String(200), nullable=True)
    price_input_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_output_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    monthly_budget_idr: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # Produk manual (PBI-23 AC3). access_note tidak disalin, sama seperti kredensial.
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    site_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Paket produk manual saat sesi dibuat (PBI-22 SCRUM-230, review #116): laporan PBI-15
    # tetap tahu paket mana yang menjawab walau Admin mengubah produknya kemudian.
    manual_params: Mapped[str | None] = mapped_column(Text, nullable=True)
    copied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseSnapshot(Base):
    """Snapshot kasus satu sesi (AC6), dibuat otomatis saat sesi dijalankan."""

    __tablename__ = "case_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_SESSION_FK), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseSnapshotItem(Base):
    """Salinan satu kasus, bentuk PBI-8: case_id ke kasus live, body dari snapshot_body()."""

    __tablename__ = "case_snapshot_items"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "case_id", name="uq_case_snapshot_items_case"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("case_snapshots.id"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    case_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("case_versions.id"), nullable=False
    )
    body: Mapped[dict] = mapped_column(_JSONB, nullable=False)


class Attempt(Base):
    """Satu percobaan per item snapshot per nomor pengulangan (AC3, AC8)."""

    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint("repetition_no BETWEEN 1 AND 5", name="ck_attempts_repetition_no"),
        # Selesai tanpa mulai tidak masuk akal. Urutan waktunya sengaja tidak dicek
        # supaya jam mundur tidak menggagalkan simpan setelah panggilan berbayar.
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name="ck_attempts_finished_needs_started",
        ),
        UniqueConstraint(
            "session_id", "snapshot_item_id", "repetition_no", name="uq_attempts_repetition"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_SESSION_FK), nullable=False
    )
    snapshot_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("case_snapshot_items.id"), nullable=False
    )
    repetition_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source: Mapped[AttemptSource] = mapped_column(
        _enum(AttemptSource, "attempt_source_enum"), nullable=False
    )
    status: Mapped[AttemptStatus] = mapped_column(
        _enum(AttemptStatus, "attempt_status_enum"),
        nullable=False,
        default=AttemptStatus.PENDING,
        server_default=AttemptStatus.PENDING.value,
    )
    # Jawaban mentah markdown. Sekali terisi tidak bisa diubah (trigger).
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Jejak waktu upaya terakhir. Coba ulang mengosongkan lalu mengisinya lagi; riwayat
    # tiap upaya ada di call_logs. NULL untuk data lama dan percobaan yang belum jalan.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # D9, percobaan manual (PBI-22). filled_at kosong = belum terisi (status D8 dihitung
    # dari sini); answered_at_platform = kapan jawaban diperoleh dari platform (AC7, AC13).
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at_platform: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # SCRUM-231: metadata jawaban manual
    filled_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=True,
    )
    platform_refused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    manual_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


class CallLog(Base):
    """Satu baris per upaya.

    attempt_id belum punya tabel induk.
    TODO(SCRUM-205): foreign key ke tabel attempt setelah tabel itu ada di staging.
    """

    __tablename__ = "call_logs"
    __table_args__ = (
        # D2: upaya ke-n per attempt (1 = awal). Coba ulang menambah upaya, bukan
        # menimpanya, dan dua dispatch tidak bisa sama-sama upaya yang sama (SCRUM-218).
        CheckConstraint("try_no >= 1", name="ck_call_logs_try_no"),
        UniqueConstraint("attempt_id", "try_no", name="uq_call_logs_attempt_try"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_idr: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    usd_idr_rate: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False)
    try_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    is_manual_retry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class AnswerHistory(Base):
    """Riwayat jawaban manual sebelum ditimpa."""

    __tablename__ = "answer_history"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("attempts.id"),
        nullable=False,
        index=True,
    )
    answer_md: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    filled_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=True,
    )
    filled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    obtained_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    platform_refused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    overwritten_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=False,
    )
    overwritten_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class AttemptEvidence(Base):
    """Bukti opsional satu jawaban manual, maks 3 per attempt (D9). PBI-22 AC7, SCRUM-233.

    Batas 3 dan "hanya percobaan manual" dicek service, lalu dijaga trigger PostgreSQL
    attempt_evidence_guard yang mengunci baris attempt. Baris tidak bisa diubah; bukti
    yang salah dihapus lalu diunggah ulang, dan keduanya dicatat di audit_logs.

    file_name bukan kolom D9 [USULAN SA]: nama asli berkas untuk unduhan.
    """

    __tablename__ = "attempt_evidence"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'file' AND file_key IS NOT NULL AND mime_type IS NOT NULL "
            "AND size_bytes IS NOT NULL AND url IS NULL) OR "
            "(kind = 'link' AND url IS NOT NULL AND file_key IS NULL "
            "AND mime_type IS NULL AND size_bytes IS NULL)",
            name="ck_attempt_evidence_kind_fields",
        ),
        CheckConstraint(
            "mime_type IS NULL OR mime_type IN ('image/png', 'image/jpeg', 'application/pdf')",
            name="ck_attempt_evidence_mime_type",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0", name="ck_attempt_evidence_size_bytes"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("attempts.id"), nullable=False, index=True
    )
    kind: Mapped[EvidenceKind] = mapped_column(
        _enum(EvidenceKind, "attempt_evidence_kind_enum"), nullable=False
    )
    # Kunci baris evidence_files; tanpa FK supaya penyimpanan bisa diganti (port EvidenceStorage).
    file_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceFile(Base):
    """Isi berkas bukti (BYTEA), dirujuk attempt_evidence.file_key. SCRUM-233.

    Di PostgreSQL, bukan volume disk: kontainer API tidak punya volume dan backup.sh hanya
    mencadangkan PostgreSQL (keputusan SA 7 November 2026). Ukurannya dibatasi
    MANUAL_EVIDENCE_MAX_BYTES.
    """

    __tablename__ = "evidence_files"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
