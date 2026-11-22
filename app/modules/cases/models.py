"""Tabel database milik modul cases.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/cases/.

TODO(SCRUM-103): column names follow that ticket, not a signed contract.
Revision c3a91e7b4d02 created the cases table (SCRUM-105). SCRUM-136 moves
the body onto case_versions. The two pointers on cases are deferred foreign
keys so a case and its first version can be inserted in one transaction.

PBI-4 SCRUM-337 (diagram D8): asal setiap kasus (manual, impor, duplikat), tabel batch
impor dan hasil per baris, dan tag dev/test yang boleh kosong selama versi masih draft.
"""

import uuid
from datetime import datetime
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
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database import Base


class SplitTag(StrEnum):
    """Dataset split stored on a version. Only dev and test are valid."""

    DEV = "dev"
    TEST = "test"


class CaseOrigin(StrEnum):
    """Cara kasus masuk ke platform (D8). Kasus lama sebelum PBI-4 semuanya manual."""

    MANUAL = "manual"
    IMPORT = "import"
    DUPLICATE = "duplicate"


class ImportRowStatus(StrEnum):
    """Hasil satu baris CSV impor (D7): jadi kasus, gagal validasi, atau dilewati kembar."""

    CREATED = "created"
    FAILED = "failed"
    SKIPPED = "skipped"


class CaseStatus(StrEnum):
    """Server-owned lifecycle. Clients do not send this on create or update."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    NEEDS_REVISION = "needs_revision"
    APPROVED = "approved"


# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


def _enum(kelas: type[StrEnum], nama: str) -> Enum:
    """Persist the enum values, not the Python member names."""
    return Enum(
        kelas,
        name=nama,
        values_callable=lambda members: [member.value for member in members],
    )


class Case(Base):
    """Identity of one legal case. The wording lives on case_versions."""

    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_suite_id", "suite_id"),
        # Kasus kembar dicari dengan sidik jari ini saat impor (D7, SCRUM-338).
        Index("ix_cases_content_fingerprint", "content_fingerprint"),
        # Asal dan rujukannya selalu berpasangan: impor punya batch, duplikat punya kasus asal.
        CheckConstraint(
            "(origin = 'import') = (import_batch_id IS NOT NULL)",
            name="ck_cases_import_batch_matches_origin",
        ),
        CheckConstraint(
            "(origin = 'duplicate') = (source_case_id IS NOT NULL)",
            name="ck_cases_source_case_matches_origin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suites.id"), nullable=False
    )
    current_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "case_versions.id",
            name="fk_cases_current_version_id",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    latest_approved_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "case_versions.id",
            name="fk_cases_latest_approved_version_id",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    # PBI-9 AC3: kasus ini pernah berada di dev set dan sudah terlihat
    # developer AI. Diisi saat Admin memindahkan dev ke test, tidak pernah dihapus.
    was_dev: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # D8: asal kasus. server_default mengisi semua kasus lama sebagai manual.
    origin: Mapped[CaseOrigin] = mapped_column(
        _enum(CaseOrigin, "case_origin_enum"),
        nullable=False,
        default=CaseOrigin.MANUAL,
        server_default=CaseOrigin.MANUAL.value,
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", name="fk_cases_import_batch_id_import_batches"),
        nullable=True,
    )
    source_case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("cases.id", name="fk_cases_source_case_id_cases"),
        nullable=True,
    )
    # fingerprint.content_fingerprint dari versi yang sedang dikerjakan; NULL bila judul dan
    # pertanyaan kosong. Tidak UNIQUE: data lama boleh sudah berisi kasus kembar.
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    updated_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    current_version: Mapped["CaseVersion"] = relationship(
        foreign_keys=[current_version_id],
        lazy="select",
    )
    latest_approved_version: Mapped["CaseVersion | None"] = relationship(
        foreign_keys=[latest_approved_version_id],
        lazy="select",
    )


class CaseVersion(Base):
    """One wording of a case. An approved row is never updated."""

    __tablename__ = "case_versions"
    __table_args__ = (
        UniqueConstraint("case_id", "version_no", name="uq_case_versions_case_id_version_no"),
        Index("ix_case_versions_case_id", "case_id"),
        # D8 dan PBI-4 AC8: tag boleh kosong hanya selama draft (hasil impor atau duplikat).
        # Pengajuan review menolak tag kosong (completeness.review_blockers), jadi versi yang
        # sudah lewat draft selalu bertag.
        CheckConstraint(
            "split_tag IS NOT NULL OR status = 'draft'",
            name="ck_case_versions_split_tag_required_after_draft",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "cases.id",
            name="fk_case_versions_case_id_cases",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CaseStatus] = mapped_column(
        _enum(CaseStatus, "case_status_enum"),
        nullable=False,
    )
    case_code: Mapped[str] = mapped_column(String(64), nullable=False)
    split_tag: Mapped[SplitTag | None] = mapped_column(
        _enum(SplitTag, "case_split_tag_enum"),
        nullable=True,
    )
    content: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_case_versions_created_by_users"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "case_versions.id",
            name="fk_case_versions_based_on_version_id",
            use_alter=True,
        ),
        nullable=True,
    )


class ImportBatch(Base):
    """Satu berkas CSV yang diimpor (D7, D8). Jumlahnya sama dengan hitungan import_row_results
    per status; dijaga service impor (SCRUM-338)."""

    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(
            "total_rows >= 0 AND success_count >= 0 AND failed_count >= 0 AND skipped_count >= 0",
            name="ck_import_batches_counts_not_negative",
        ),
        CheckConstraint(
            "success_count + failed_count + skipped_count <= total_rows",
            name="ck_import_batches_counts_within_total",
        ),
        CheckConstraint("length(trim(file_name)) > 0", name="ck_import_batches_file_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_import_batches_imported_by_users"),
        nullable=False,
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Keputusan SA: satu suite sementara per batch, supaya batch bisa ditinjau atau dibuang utuh.
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("suites.id", name="fk_import_batches_suite_id_suites"),
        nullable=False,
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class ImportRowResult(Base):
    """Hasil satu baris CSV (D7): kasus yang dibuat, atau alasan gagal atau dilewati (AC7)."""

    __tablename__ = "import_row_results"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_no", name="uq_import_row_results_batch_id_row_no"),
        CheckConstraint("row_no >= 1", name="ck_import_row_results_row_no"),
        # Hanya baris created yang menunjuk kasus; baris lain tidak pernah membuat kasus (AC8).
        CheckConstraint(
            "(status = 'created') = (case_id IS NOT NULL)",
            name="ck_import_row_results_case_only_when_created",
        ),
        # Baris yang gagal atau dilewati selalu punya alasan untuk ringkasan (AC7).
        CheckConstraint(
            "status = 'created' OR length(trim(coalesce(reason, ''))) > 0",
            name="ck_import_row_results_reason_when_not_created",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", name="fk_import_row_results_batch_id_import_batches"),
        nullable=False,
    )
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(
        _enum(ImportRowStatus, "import_row_status_enum"), nullable=False
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("cases.id", name="fk_import_row_results_case_id_cases"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Keputusan SA: sidik jari baris, untuk mencari kembar di dalam berkas yang sama.
    row_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
