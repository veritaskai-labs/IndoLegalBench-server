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
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
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
    """Status sesi per D4. Perpindahan yang sah ada di session_transitions (SCRUM-214).

    paused mencakup Jeda dari pengguna dan sesi yang dihentikan sistem (restart, deploy,
    anggaran habis); D4 tidak memisahkan Dihentikan dari Dijeda.
    """

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FINISHED = "finished"
    FINISHED_WITH_FAILURES = "finished_with_failures"
    FAILED = "failed"


class StopReason(StrEnum):
    """Penyebab sesi berhenti (D2 stop_reason). paused: user, budget, restart; failed: error."""

    USER = "user"
    BUDGET = "budget"
    RESTART = "restart"
    ERROR = "error"


class AttemptSource(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


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
    # Penilai LLM (AC1). Tabel judge_providers milik PBI-14, jadi belum ber-FK.
    judge_provider_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CallLog(Base):
    """Satu baris per upaya.

    attempt_id belum punya tabel induk.
    TODO(SCRUM-205): foreign key ke tabel attempt setelah tabel itu ada di staging.
    """

    __tablename__ = "call_logs"

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
