"""Tabel database milik modul runs.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/runs/.

Semua model wajib mewarisi Base dari app.shared.database supaya
terdeteksi Alembic.

PBI-11 SCRUM-76 (SCRUM-205), D2. Tabelnya dibuat migrasi 16014b26f0bf, yang
juga memasang trigger PostgreSQL (sesi tidak bisa dihapus, pilihan suite,
salinan produk, dan snapshot hanya INSERT, jawaban percobaan permanen).
Model di sini harus sama dengan migrasi itu.

CallLog (SCRUM-209) dibuat migrasi d3c8a1e54b20.
Attempt timestamps (SCRUM-259) dibuat migrasi b3c91d7a5e24.
Manual answer history (SCRUM-231) dibuat migrasi d8a531f0c231.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
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
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.text import sql_not_blank

_JSONB = JSON().with_variant(JSONB(), "postgresql")
_USER_FK = "users.id"
_SESSION_FK = "measurement_sessions.id"


def _enum_values(members) -> list[str]:
    return [member.value for member in members]


class SessionPurpose(StrEnum):
    """Pengukuran Resmi atau Uji Coba."""

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
    """Status sesi otomatis dan manual."""

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
    return Enum(
        enum_cls,
        name=name,
        values_callable=_enum_values,
    )


_REASON_NOT_BLANK = sql_not_blank("coalesce(accepted_as_is_reason, '')")

_RUNNING_AUTOMATIC = text("status = 'running' AND session_type = 'automatic'")


class MeasurementSession(Base):
    """Satu sesi pengukuran untuk satu produk AI."""

    __tablename__ = "measurement_sessions"

    __table_args__ = (
        CheckConstraint(
            "repetitions BETWEEN 1 AND 5",
            name="ck_measurement_sessions_repetitions",
        ),
        CheckConstraint(
            sql_not_blank("label"),
            name="ck_measurement_sessions_label_not_blank",
        ),
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
        CheckConstraint(
            "NOT (status = 'running' AND archived_at IS NOT NULL)",
            name="ck_measurement_sessions_running_not_archived",
        ),
        CheckConstraint(
            "(stopped_at IS NULL) = (stop_reason IS NULL)",
            name="ck_measurement_sessions_stop_complete",
        ),
        CheckConstraint(
            "(finalized_at IS NULL) = (finalized_by IS NULL)",
            name="ck_measurement_sessions_finalized_complete",
        ),
        Index(
            "ix_measurement_sessions_product_id",
            "product_id",
        ),
        Index(
            "uq_measurement_sessions_one_running_automatic",
            "session_type",
            unique=True,
            postgresql_where=_RUNNING_AUTOMATIC,
            sqlite_where=_RUNNING_AUTOMATIC,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    label: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    purpose: Mapped[SessionPurpose] = mapped_column(
        _enum(SessionPurpose, "measurement_purpose_enum"),
        nullable=False,
    )

    session_type: Mapped[SessionType] = mapped_column(
        _enum(SessionType, "measurement_session_type_enum"),
        nullable=False,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_products.id"),
        nullable=False,
    )

    repetitions: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )

    split_filter: Mapped[SplitFilter] = mapped_column(
        _enum(SplitFilter, "measurement_split_filter_enum"),
        nullable=False,
    )

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
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    stop_reason: Mapped[StopReason | None] = mapped_column(
        _enum(StopReason, "measurement_stop_reason_enum"),
        nullable=True,
    )

    resumed_after_long_pause: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    model_version_unverified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    resume_version_check_pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    resume_error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=True,
    )

    accepted_as_is_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    accepted_as_is_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=True,
    )

    accepted_as_is_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    answer_date_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    answer_date_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_USER_FK),
        nullable=True,
    )


class MeasurementSessionSuite(Base):
    """Suite yang dipilih untuk sesi."""

    __tablename__ = "measurement_session_suites"

    __table_args__ = (
        Index(
            "ix_measurement_session_suites_suite_id",
            "suite_id",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_SESSION_FK),
        primary_key=True,
    )

    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("suites.id"),
        primary_key=True,
    )


class SessionProductCopy(Base):
    """Salinan data produk saat sesi dibuat, tanpa kredensial."""

    __tablename__ = "session_product_copies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_SESSION_FK),
        nullable=False,
        unique=True,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_products.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    measurement_type: Mapped[str] = mapped_column(
        Enum(
            "automatic",
            "manual",
            name="ai_product_measurement_type_enum",
        ),
        nullable=False,
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

    base_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    model_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    model_params: Mapped[dict | None] = mapped_column(
        _JSONB,
        nullable=True,
    )

    model_version_at_start: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    price_input_usd_per_mtok: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
    )

    price_output_usd_per_mtok: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
    )

    monthly_budget_idr: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    vendor_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    site_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    manual_params: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    copied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class CaseSnapshot(Base):
    """Snapshot kasus untuk satu sesi."""

    __tablename__ = "case_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_SESSION_FK),
        nullable=False,
        unique=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class CaseSnapshotItem(Base):
    """Snapshot satu kasus berdasarkan case_version."""

    __tablename__ = "case_snapshot_items"

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "case_id",
            name="uq_case_snapshot_items_case",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_snapshots.id"),
        nullable=False,
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("cases.id"),
        nullable=False,
    )

    case_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_versions.id"),
        nullable=False,
    )

    body: Mapped[dict] = mapped_column(
        _JSONB,
        nullable=False,
    )


class Attempt(Base):
    """Satu percobaan per item snapshot per nomor pengulangan."""

    __tablename__ = "attempts"

    __table_args__ = (
        CheckConstraint(
            "repetition_no BETWEEN 1 AND 5",
            name="ck_attempts_repetition_no",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name="ck_attempts_finished_needs_started",
        ),
        UniqueConstraint(
            "session_id",
            "snapshot_item_id",
            "repetition_no",
            name="uq_attempts_repetition",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(_SESSION_FK),
        nullable=False,
    )

    snapshot_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_snapshot_items.id"),
        nullable=False,
    )

    repetition_no: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )

    source: Mapped[AttemptSource] = mapped_column(
        _enum(AttemptSource, "attempt_source_enum"),
        nullable=False,
    )

    status: Mapped[AttemptStatus] = mapped_column(
        _enum(AttemptStatus, "attempt_status_enum"),
        nullable=False,
        default=AttemptStatus.PENDING,
        server_default=AttemptStatus.PENDING.value,
    )

    answer_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    model_version: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # SCRUM-259: attempt execution timestamps
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # SCRUM-230: manual measurement metadata
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    answered_at_platform: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # SCRUM-231: manual answer metadata
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
    """Satu baris untuk setiap upaya panggilan provider."""

    __tablename__ = "call_logs"

    __table_args__ = (
        CheckConstraint(
            "try_no >= 1",
            name="ck_call_logs_try_no",
        ),
        UniqueConstraint(
            "attempt_id",
            "try_no",
            name="uq_call_logs_attempt_try",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    cost_idr: Mapped[object | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    usd_idr_rate: Mapped[object] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    model_version: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    result: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    failure_kind: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    raw: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    try_no: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    is_manual_retry: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )


class AnswerHistory(Base):
    """Riwayat jawaban manual sebelum ditimpa (SCRUM-231)."""

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
