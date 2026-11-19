"""Tabel database milik modul reports.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/reports/.

Semua model wajib mewarisi Base dari app.shared.database supaya
terdeteksi Alembic.

PBI-15 SCRUM-79 (SCRUM-300), D1 "ERD Laporan dan Penerbitan". Tabelnya dibuat migrasi
b6f3a2d81c47. Laporan disimpan beku: angka dihitung sekali saat laporan dibuat (SCRUM-313)
lalu hanya dibaca (SCRUM-320, PBI-16). Enum di sini menjadi sumber nilai untuk schemas.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.cases.models import SplitTag
from app.shared.database import Base

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")
_USER_FK = "users.id"
_REPORT_FK = "reports.id"


def _enum_values(members) -> list[str]:
    return [member.value for member in members]


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


class ScoreScope(StrEnum):
    """Jenis nilai yang dipilih saat membuat laporan (AC14)."""

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
    """Label otomatis/manual per produk, salinan measurement_sessions.session_type (AC5)."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ProductLabelMode(StrEnum):
    """Nama asli atau kode produk, dipilih Admin saat membuat laporan (AC9)."""

    REAL_NAME = "real_name"
    CODE = "code"


class ReportStatus(StrEnum):
    SAVED = "saved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class ReportLanguage(StrEnum):
    """Bahasa laporan dan PDF-nya. Bawaan Inggris (PBI-26 AC1)."""

    EN = "en"
    ID = "id"


class Report(Base):
    """Satu laporan perbandingan tersimpan (AC10). Hanya status yang boleh berubah."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    score_scope: Mapped[ScoreScope] = mapped_column(
        _enum(ScoreScope, "report_score_scope_enum"), nullable=False
    )
    has_dev_scores: Mapped[bool] = mapped_column(Boolean, nullable=False)
    measurement_filter: Mapped[MeasurementFilter] = mapped_column(
        _enum(MeasurementFilter, "report_measurement_filter_enum"), nullable=False
    )
    product_label_mode: Mapped[ProductLabelMode] = mapped_column(
        _enum(ProductLabelMode, "report_label_mode_enum"), nullable=False
    )
    language: Mapped[ReportLanguage] = mapped_column(
        _enum(ReportLanguage, "report_language_enum"),
        nullable=False,
        default=ReportLanguage.EN,
        server_default=ReportLanguage.EN.value,
    )
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status_enum"),
        nullable=False,
        default=ReportStatus.SAVED,
        server_default=ReportStatus.SAVED.value,
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rubric_versions.id"), nullable=False
    )
    judge_model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    case_basis_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReportSession(Base):
    """Satu sesi (satu produk) di laporan beserta label dan penandanya yang dibekukan."""

    __tablename__ = "report_sessions"

    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_REPORT_FK), primary_key=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("measurement_sessions.id"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_products.id"), nullable=False
    )
    product_label: Mapped[str] = mapped_column(String(200), nullable=False)
    measurement_kind: Mapped[MeasurementKind] = mapped_column(
        _enum(MeasurementKind, "report_measurement_kind_enum"), nullable=False
    )
    is_trial: Mapped[bool] = mapped_column(Boolean, nullable=False)
    markers: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)


class ReportResult(Base):
    """Angka beku satu sesi untuk satu jenis skor (AC6, AC2, AC3)."""

    __tablename__ = "report_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["report_id", "session_id"],
            ["report_sessions.report_id", "report_sessions.session_id"],
            name="fk_report_results_report_session",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    score_split: Mapped[ScoreSplit] = mapped_column(
        _enum(ScoreSplit, "report_score_split_enum"), nullable=False
    )
    cases_graded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cases_basis_count: Mapped[int] = mapped_column(Integer, nullable=False)
    det_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    llm_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    failure_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    rank: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    repetitions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    product_model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    judge_model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ReportCaseBasis(Base):
    """Satu kasus dasar perbandingan: reference id + versi (D1 aturan 7, PBI-16 AC1)."""

    __tablename__ = "report_case_basis"

    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_REPORT_FK), primary_key=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cases.id"), primary_key=True
    )
    case_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("case_versions.id"), nullable=False
    )
    split_tag: Mapped[SplitTag] = mapped_column(
        _enum(SplitTag, "case_split_tag_enum"), nullable=False
    )


class PublishedReport(Base):
    """Satu versi terbit (PBI-17 AC9). Versi baru menunjuk versi lama lewat supersedes_id."""

    __tablename__ = "published_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_REPORT_FK), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey(_USER_FK), nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("published_reports.id"), nullable=True
    )
