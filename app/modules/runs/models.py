"""Tabel database milik modul runs.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/runs/.

Semua model wajib mewarisi Base dari app.shared.database supaya
terdeteksi Alembic.

PBI-11 SCRUM-76 (SCRUM-205), D2. Tabelnya dibuat migrasi 16014b26f0bf, yang
juga memasang trigger PostgreSQL (sesi tidak bisa dihapus, snapshot hanya
INSERT, jawaban percobaan permanen). Model di sini harus sama dengan migrasi
itu; tests/integration/test_measurement_models_pg.py memeriksanya.
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

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


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
    """Status dasar. Dihentikan dan Dibatalkan menyusul di PBI-12."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FINISHED_WITH_FAILURES = "finished_with_failures"
    FAILED = "failed"


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


_RUNNING_AUTOMATIC = text("status = 'running' AND session_type = 'automatic'")


class MeasurementSession(Base):
    """Satu sesi = satu produk AI (AC1). Tidak pernah dihapus, hanya diarsipkan (AC10)."""

    __tablename__ = "measurement_sessions"
    __table_args__ = (
        CheckConstraint("repetitions BETWEEN 1 AND 5", name="ck_measurement_sessions_repetitions"),
        CheckConstraint("length(trim(label)) > 0", name="ck_measurement_sessions_label_not_blank"),
        CheckConstraint(
            "(archived_at IS NULL) = (archived_by IS NULL)",
            name="ck_measurement_sessions_archived_complete",
        ),
        CheckConstraint(
            "(accepted_as_is_at IS NULL AND accepted_as_is_by IS NULL "
            "AND accepted_as_is_reason IS NULL) OR "
            "(accepted_as_is_at IS NOT NULL AND accepted_as_is_by IS NOT NULL "
            "AND length(trim(coalesce(accepted_as_is_reason, ''))) > 0)",
            name="ck_measurement_sessions_accepted_as_is_complete",
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
    status: Mapped[SessionStatus] = mapped_column(
        _enum(SessionStatus, "measurement_session_status_enum"),
        nullable=False,
        default=SessionStatus.QUEUED,
        server_default=SessionStatus.QUEUED.value,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
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
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    accepted_as_is_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_as_is_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    accepted_as_is_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class MeasurementSessionSuite(Base):
    """Suite yang dipilih untuk sesi (AC2). Kasus dipilih per suite, bukan per kasus."""

    __tablename__ = "measurement_session_suites"
    __table_args__ = (Index("ix_measurement_session_suites_suite_id", "suite_id"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("measurement_sessions.id"), primary_key=True
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suites.id"), primary_key=True
    )


class SessionProductCopy(Base):
    """Data produk saat sesi dibuat (AC6). Kredensial sengaja tidak disalin."""

    __tablename__ = "session_product_copies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("measurement_sessions.id"), nullable=False, unique=True
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
    copied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseSnapshot(Base):
    """Snapshot kasus satu sesi (AC6), dibuat otomatis saat sesi dijalankan."""

    __tablename__ = "case_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("measurement_sessions.id"), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseSnapshotItem(Base):
    """Salinan satu kasus, bentuk PBI-8: case_id ke kasus live, body dari snapshot_body()."""

    __tablename__ = "case_snapshot_items"

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
        Uuid(as_uuid=True), ForeignKey("measurement_sessions.id"), nullable=False
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
