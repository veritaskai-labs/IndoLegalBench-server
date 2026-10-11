"""Tabel database milik modul reviews.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/reviews/.
Modul lain memanggil reviews.service.

PBI-6, SCRUM-143. Bentuk tabel mengikuti D4a: satu round per pengajuan
versi kasus, assignment per reviewer, satu verdict per assignment.

review_rounds menyimpan case_id di samping case_version_id, sama seperti
suite_snapshot_items. Dengan begitu pencatat audit dan cakupan Reviewer
(PBI-18 AC5) bisa menemukan kasusnya tanpa membaca tabel milik modul cases.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


class RoundStatus(StrEnum):
    """awaiting_assignment sampai dua reviewer aktif terpasang (SCRUM-144)."""

    AWAITING_ASSIGNMENT = "awaiting_assignment"
    OPEN = "open"
    DECIDED = "decided"


class RoundDecision(StrEnum):
    APPROVED = "approved"
    REVISE = "revise"


class AssignmentSource(StrEnum):
    SYSTEM = "system"
    ADMIN = "admin"


class AssignmentStatus(StrEnum):
    """replaced: diganti Admin (AC7). void: reviewer dinonaktifkan (AC8)."""

    ACTIVE = "active"
    REPLACED = "replaced"
    VOID = "void"


class VerdictDecision(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"


def _enum(kelas: type[StrEnum], nama: str) -> Enum:
    """Simpan nilai enum, bukan nama member Python."""
    return Enum(
        kelas,
        name=nama,
        values_callable=lambda members: [member.value for member in members],
    )


class ReviewRound(Base):
    """Satu putaran review untuk satu versi kasus."""

    __tablename__ = "review_rounds"
    __table_args__ = (
        UniqueConstraint(
            "case_version_id", "round_no", name="uq_review_rounds_case_version_id_round_no"
        ),
        Index("ix_review_rounds_case_id", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cases.id", name="fk_review_rounds_case_id"), nullable=False
    )
    case_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("case_versions.id", name="fk_review_rounds_case_version_id"),
        nullable=False,
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RoundStatus] = mapped_column(
        _enum(RoundStatus, "review_round_status_enum"),
        nullable=False,
        default=RoundStatus.AWAITING_ASSIGNMENT,
    )
    decision: Mapped[RoundDecision | None] = mapped_column(
        _enum(RoundDecision, "review_round_decision_enum"), nullable=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewAssignment(Base):
    """Satu reviewer yang ditugaskan di satu round."""

    __tablename__ = "review_assignments"
    __table_args__ = (
        # D4a: satu reviewer hanya boleh aktif sekali per round. Assignment
        # yang replaced atau void tidak ikut, jadi reviewer bisa dipasang lagi.
        Index(
            "uq_review_assignments_active_reviewer",
            "review_round_id",
            "reviewer_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_review_assignments_reviewer_id", "reviewer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_round_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_rounds.id", name="fk_review_assignments_review_round_id"),
        nullable=False,
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_review_assignments_reviewer_id"),
        nullable=False,
    )
    source: Mapped[AssignmentSource] = mapped_column(
        _enum(AssignmentSource, "review_assignment_source_enum"),
        nullable=False,
        default=AssignmentSource.SYSTEM,
    )
    # Kosong bila source = system (D4a).
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_review_assignments_assigned_by"),
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        _enum(AssignmentStatus, "review_assignment_status_enum"),
        nullable=False,
        default=AssignmentStatus.ACTIVE,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewVerdict(Base):
    """Penilaian satu assignment. Komentar wajib bila revise (D4a)."""

    __tablename__ = "review_verdicts"
    __table_args__ = (
        CheckConstraint(
            "decision <> 'revise' OR (comment IS NOT NULL AND length(trim(comment)) > 0)",
            name="ck_review_verdicts_revise_needs_comment",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_assignments.id", name="fk_review_verdicts_assignment_id"),
        nullable=False,
        unique=True,
    )
    decision: Mapped[VerdictDecision] = mapped_column(
        _enum(VerdictDecision, "review_verdict_decision_enum"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Round 2 membawa approve reviewer yang tidak meminta revisi (AC9, D3).
    carried_from_verdict_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_verdicts.id", name="fk_review_verdicts_carried_from_verdict_id"),
        nullable=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
