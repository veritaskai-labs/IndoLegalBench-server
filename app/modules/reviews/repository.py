"""Akses database modul reviews.

ATURAN: hanya reviews/service.py yang boleh memanggil file ini. Isi file
ini murni query, tanpa logika bisnis, dan tidak pernah commit: transaksi
milik pemanggil di service.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.reviews.models import ReviewAssignment, ReviewRound, ReviewVerdict


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
