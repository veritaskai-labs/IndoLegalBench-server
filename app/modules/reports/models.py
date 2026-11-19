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
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.cases.models import SplitTag
from app.shared.database import Base
from app.shared.text import sql_not_blank

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")
_USER_FK = "users.id"
_REPORT_FK = "reports.id"


def _enum_values(members) -> list[str]:
    return [member.value for member in members]


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=_enum_values)


def _score_in_range(column: str) -> str:
    """Skor 0..1, skala yang sama dengan grading_results dan llm_judge_results."""
    return f"{column} IS NULL OR {column} BETWEEN 0 AND 1"


_RANKED = text("rank IS NOT NULL")


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


class ReportMarkerCode(StrEnum):
    """Kode penanda D2 per sesi di report_sessions.markers (keputusan SA, 19 Nov).

    Uji Coba tidak punya kode di sini: disimpan hanya di is_trial supaya sumbernya satu.
    """

    MODEL_VERSION_DIFFERS = "model_version_differs"
    REPETITIONS_DIFFER = "repetitions_differ"
    MANUAL_DATE_SPAN_OVER_7_DAYS = "manual_date_span_over_7_days"
    REFUSALS = "refusals"


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
    __table_args__ = (
        CheckConstraint(sql_not_blank("title"), name="ck_reports_title_not_blank"),
        CheckConstraint(
            sql_not_blank("judge_model_name"), name="ck_reports_judge_model_name_not_blank"
        ),
        # Penanda dev tidak boleh berbohong tentang jenis nilai (AC7, AC14).
        CheckConstraint("has_dev_scores = (score_scope <> 'test')", name="ck_reports_dev_flag"),
        # Irisan kasus kosong hanya memberi peringatan dan tidak pernah disimpan (AC1, D2).
        CheckConstraint("case_basis_count > 0", name="ck_reports_case_basis_count"),
        Index("ix_reports_created_by_created_at", "created_by", "created_at"),
        Index("ix_reports_status_created_at", "status", "created_at"),
    )

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
    __table_args__ = (
        # Satu produk hanya diwakili satu sesi dalam satu laporan (D1 keputusan SA).
        UniqueConstraint("report_id", "product_id", name="uq_report_sessions_product"),
        CheckConstraint(
            sql_not_blank("product_label"), name="ck_report_sessions_product_label_not_blank"
        ),
        Index("ix_report_sessions_session_id", "session_id"),
    )

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
        UniqueConstraint("report_id", "session_id", "score_split", name="uq_report_results_split"),
        CheckConstraint(
            " AND ".join(
                f"({_score_in_range(c)})" for c in ("det_score", "llm_score", "total_score")
            ),
            name="ck_report_results_scores",
        ),
        CheckConstraint(
            "failure_rate IS NULL OR failure_rate BETWEEN 0 AND 100",
            name="ck_report_results_failure_rate",
        ),
        CheckConstraint(
            "cases_basis_count >= 0 AND cases_basis_count <= cases_graded_count",
            name="ck_report_results_counts",
        ),
        CheckConstraint("rank IS NULL OR rank >= 1", name="ck_report_results_rank"),
        # D1: kalau salah satu skor kosong, total dan peringkat sama-sama kosong.
        CheckConstraint(
            "(total_score IS NULL) = (rank IS NULL)", name="ck_report_results_rank_total"
        ),
        CheckConstraint("repetitions BETWEEN 1 AND 5", name="ck_report_results_repetitions"),
        # Seri sudah diputus SCRUM-313 (llm_score lalu det_score), jadi peringkat unik.
        Index(
            "uq_report_results_rank",
            "report_id",
            "score_split",
            "rank",
            unique=True,
            postgresql_where=_RANKED,
            sqlite_where=_RANKED,
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
    __table_args__ = (
        # Satu laporan terbit paling banyak sekali; mengganti = laporan baru (laporan beku).
        UniqueConstraint("report_id", name="uq_published_reports_report"),
        # Satu versi hanya diganti sekali, jadi rantai versi tidak bercabang.
        UniqueConstraint("supersedes_id", name="uq_published_reports_supersedes"),
        CheckConstraint("version_no >= 1", name="ck_published_reports_version_no"),
        CheckConstraint(
            "(supersedes_id IS NULL) = (version_no = 1)", name="ck_published_reports_chain"
        ),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="ck_published_reports_not_self"
        ),
    )

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
