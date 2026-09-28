"""Tabel database milik modul cases.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/cases/.

TODO(SCRUM-103): column names follow that ticket, not a signed contract.
Revision c3a91e7b4d02 is the one cases migration (SCRUM-105). Alter it for any
rename until it has run on a shared database; after that, add a new revision.
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
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


class SplitTag(StrEnum):
    """Dataset split stored on a case. Only dev and test are valid."""

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
    """One legal case."""

    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_suite_id", "suite_id"),
        Index("ix_cases_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suites.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    legal_refs: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    answer_criteria: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    traps: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    split_tag: Mapped[SplitTag] = mapped_column(
        _enum(SplitTag, "case_split_tag_enum"), nullable=False
    )
    status: Mapped[CaseStatus] = mapped_column(
        _enum(CaseStatus, "case_status_enum"),
        nullable=False,
        default=CaseStatus.DRAFT,
        server_default=CaseStatus.DRAFT.value,
    )
    completeness: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
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
