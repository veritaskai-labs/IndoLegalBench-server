"""Akses database modul reviews.

ATURAN: hanya reviews/service.py yang boleh memanggil file ini. Isi file
ini murni query, tanpa logika bisnis, dan tidak pernah commit: transaksi
milik pemanggil di service.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
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


def latest_round(db: Session, case_version_id: uuid.UUID) -> ReviewRound | None:
    """Round dengan nomor tertinggi untuk versi ini, atau None."""
    return db.scalar(
        select(ReviewRound)
        .where(ReviewRound.case_version_id == case_version_id)
        .order_by(ReviewRound.round_no.desc())
        .limit(1)
    )


def case_ids_assigned_to(db: Session, reviewer_id: uuid.UUID) -> Sequence[uuid.UUID]:
    """Kasus yang pernah ditugaskan ke reviewer ini, apa pun status assignment-nya."""
    return db.scalars(
        select(ReviewRound.case_id)
        .join(ReviewAssignment, ReviewAssignment.review_round_id == ReviewRound.id)
        .where(ReviewAssignment.reviewer_id == reviewer_id)
        .distinct()
    ).all()


def get_assignment(
    db: Session, assignment_id: uuid.UUID, *, fresh: bool = False
) -> ReviewAssignment | None:
    """Satu assignment beserta round dan verdict-nya, atau None.

    fresh=True menimpa nilai yang sudah ada di session dengan isi database
    saat ini. Dipakai setelah round dikunci, supaya penggantian reviewer
    yang baru di-commit transaksi lain ikut terlihat.
    """
    query = (
        select(ReviewAssignment)
        .options(joinedload(ReviewAssignment.review_round), joinedload(ReviewAssignment.verdict))
        .where(ReviewAssignment.id == assignment_id)
    )
    if fresh:
        query = query.execution_options(populate_existing=True)
    return db.scalar(query)


def lock_round(db: Session, round_id: uuid.UUID) -> ReviewRound:
    """Kunci baris round sampai transaksi selesai (SELECT ... FOR UPDATE).

    Dua verdict untuk round yang sama jadi antre, jadi verdict kedua selalu
    melihat verdict pertama saat menghitung keputusan. SQLite mengabaikan
    FOR UPDATE; perilakunya diuji di PostgreSQL.
    """
    return db.scalars(
        select(ReviewRound)
        .where(ReviewRound.id == round_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def assignments_in_round(db: Session, round_id: uuid.UUID) -> Sequence[ReviewAssignment]:
    """Semua assignment satu round, apa pun statusnya, beserta verdict-nya."""
    return db.scalars(
        select(ReviewAssignment)
        .options(joinedload(ReviewAssignment.verdict))
        .where(ReviewAssignment.review_round_id == round_id)
        .order_by(ReviewAssignment.assigned_at, ReviewAssignment.id)
        .execution_options(populate_existing=True)
    ).all()


def open_assignments_for(db: Session, reviewer_id: uuid.UUID) -> Sequence[ReviewAssignment]:
    """Tugas aktif reviewer ini di round yang masih open, round terlama lebih dulu."""
    return db.scalars(
        select(ReviewAssignment)
        .join(ReviewAssignment.review_round)
        .options(joinedload(ReviewAssignment.review_round), joinedload(ReviewAssignment.verdict))
        .where(
            ReviewAssignment.reviewer_id == reviewer_id,
            ReviewAssignment.status == AssignmentStatus.ACTIVE,
            ReviewRound.status == RoundStatus.OPEN,
        )
        .order_by(ReviewRound.opened_at, ReviewAssignment.id)
    ).all()


def latest_round_for_case(db: Session, case_id: uuid.UUID) -> ReviewRound | None:
    """Round yang paling baru dibuka untuk kasus ini, lintas versi."""
    return db.scalars(
        select(ReviewRound)
        .where(ReviewRound.case_id == case_id)
        .order_by(ReviewRound.opened_at.desc(), ReviewRound.round_no.desc())
        .limit(1)
    ).first()


def add_verdict(db: Session, verdict: ReviewVerdict) -> None:
    """Tambahkan verdict lalu flush supaya UQ assignment_id langsung ketahuan."""
    db.add(verdict)
    db.flush()
