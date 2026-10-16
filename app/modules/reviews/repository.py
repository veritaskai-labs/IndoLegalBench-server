"""Akses database modul reviews.

ATURAN: hanya reviews/service.py yang boleh memanggil file ini. Isi file
ini murni query, tanpa logika bisnis, dan tidak pernah commit: transaksi
milik pemanggil di service.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
)


def max_round_no(db: Session, case_version_id: uuid.UUID) -> int:
    """Nomor round tertinggi untuk satu versi, atau 0 bila belum pernah diajukan."""
    tertinggi = db.scalar(
        select(func.max(ReviewRound.round_no)).where(ReviewRound.case_version_id == case_version_id)
    )
    return int(tertinggi or 0)


def add_round(db: Session, ronde: ReviewRound) -> None:
    """Tambahkan round lalu flush supaya bentrok nomor round langsung ketahuan."""
    db.add(ronde)
    db.flush()


def get_round(db: Session, round_id: uuid.UUID) -> ReviewRound | None:
    """Satu round, atau None bila id-nya tidak ada."""
    return db.get(ReviewRound, round_id)


def reviewer_assignments(
    db: Session, reviewer_ids: Sequence[uuid.UUID]
) -> Sequence[tuple[ReviewAssignment, bool]]:
    """Setiap penugasan reviewer ini, plus apakah sudah punya verdict."""
    if not reviewer_ids:
        return []
    baris = db.execute(
        select(ReviewAssignment, ReviewVerdict.id.is_not(None))
        .outerjoin(ReviewVerdict, ReviewVerdict.assignment_id == ReviewAssignment.id)
        .where(ReviewAssignment.reviewer_id.in_(reviewer_ids))
    ).all()
    return [(tugas, bool(punya_verdict)) for tugas, punya_verdict in baris]


def add_assignment(db: Session, tugas: ReviewAssignment) -> None:
    """Tambahkan penugasan. Pemanggil yang flush, supaya satu round cukup sekali."""
    db.add(tugas)


def case_ids_assigned_to(db: Session, reviewer_id: uuid.UUID) -> Sequence[uuid.UUID]:
    """Kasus yang pernah ditugaskan ke reviewer ini, apa pun status assignment-nya."""
    return db.scalars(
        select(ReviewRound.case_id)
        .join(ReviewAssignment, ReviewAssignment.review_round_id == ReviewRound.id)
        .where(ReviewAssignment.reviewer_id == reviewer_id)
        .distinct()
    ).all()


def get_assignment(db: Session, assignment_id: uuid.UUID) -> ReviewAssignment | None:
    """Satu penugasan beserta round-nya, atau None bila id-nya tidak ada."""
    return (
        db.query(ReviewAssignment)
        .options(joinedload(ReviewAssignment.review_round))
        .filter(ReviewAssignment.id == assignment_id)
        .one_or_none()
    )


def get_active_assignments_by_reviewer(
    db: Session, reviewer_id: uuid.UUID
) -> list[ReviewAssignment]:
    """Semua penugasan active reviewer beserta round-nya."""
    return list(
        db.query(ReviewAssignment)
        .options(joinedload(ReviewAssignment.review_round))
        .filter(
            ReviewAssignment.reviewer_id == reviewer_id,
            ReviewAssignment.status == AssignmentStatus.ACTIVE,
        )
        .all()
    )


def get_assignments_for_round(db: Session, round_id: uuid.UUID) -> list[ReviewAssignment]:
    """Seluruh assignment dalam satu round, diurutkan waktu penugasan."""
    return list(
        db.query(ReviewAssignment)
        .filter(ReviewAssignment.review_round_id == round_id)
        .order_by(ReviewAssignment.assigned_at.asc())
        .all()
    )


def get_active_assignments_for_round(db: Session, round_id: uuid.UUID) -> list[ReviewAssignment]:
    """Assignment aktif dalam satu round."""
    return list(
        db.query(ReviewAssignment)
        .filter(
            ReviewAssignment.review_round_id == round_id,
            ReviewAssignment.status == AssignmentStatus.ACTIVE,
        )
        .order_by(ReviewAssignment.assigned_at.asc())
        .all()
    )


def get_rounds_needing_reassignment(db: Session) -> list[ReviewRound]:
    """Round aktif yang memerlukan penugasan ulang (AC8)."""
    void_rounds = (
        select(ReviewAssignment.review_round_id)
        .where(ReviewAssignment.status == AssignmentStatus.VOID)
        .scalar_subquery()
    )
    return list(
        db.scalars(
            select(ReviewRound)
            .where(
                ReviewRound.decision.is_(None),
                ReviewRound.status != RoundStatus.DECIDED,
                or_(
                    ReviewRound.status == RoundStatus.AWAITING_ASSIGNMENT,
                    ReviewRound.id.in_(void_rounds),
                ),
            )
            .order_by(ReviewRound.opened_at.asc())
        ).all()
    )
