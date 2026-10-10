"""Tabel database milik modul cases.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/cases/.

TODO(SCRUM-103): column names follow that ticket, not a signed contract.
Revision c3a91e7b4d02 created the cases table (SCRUM-105). SCRUM-136 moves
the body onto case_versions. The two pointers on cases are deferred foreign
keys so a case and its first version can be inserted in one transaction.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database import Base


class SplitTag(StrEnum):
    """Dataset split stored on a version. Only dev and test are valid."""

    DEV = "dev"
    TEST = "test"


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
    __table_args__ = (Index("ix_cases_suite_id", "suite_id"),)

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
    split_tag: Mapped[SplitTag] = mapped_column(
        _enum(SplitTag, "case_split_tag_enum"),
        nullable=False,
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
