"""Akses database modul reviews.

ATURAN: hanya reviews/service.py yang boleh memanggil file ini. Isi file
ini murni query, tanpa logika bisnis, dan tidak pernah commit: transaksi
milik pemanggil di service.
"""

import uuid
from collections.abc import Collection, Sequence

from sqlalchemy import and_, func, or_, select
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


def verdict_owners(
    db: Session, assignment_ids: Collection[uuid.UUID]
) -> Sequence[tuple[uuid.UUID, uuid.UUID, RoundStatus]]:
    """Reviewer pemilik dan status round tiap assignment."""
    return (
        db.execute(
            select(ReviewAssignment.id, ReviewAssignment.reviewer_id, ReviewRound.status)
            .join(ReviewRound, ReviewRound.id == ReviewAssignment.review_round_id)
            .where(ReviewAssignment.id.in_(assignment_ids))
        )
        .tuples()
        .all()
    )


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


def count_cases_waiting(db: Session, *, slots: int) -> int:
    """Kasus yang round berjalannya punya kurang dari `slots` verdict sah (PBI-19 AC1).

    Round yang belum decided selalu milik versi in_review, karena keputusan
    round ikut mengubah status versi. Verdict sah = verdict pada assignment
    active, jadi verdict reviewer replaced (AC7) atau void tidak dihitung,
    dan round tanpa reviewer (awaiting_assignment) ikut terhitung.
    """
    verdict_sah = (
        select(func.count(ReviewVerdict.id))
        .join(ReviewAssignment, ReviewAssignment.id == ReviewVerdict.assignment_id)
        .where(
            ReviewAssignment.review_round_id == ReviewRound.id,
            ReviewAssignment.status == AssignmentStatus.ACTIVE,
        )
        .correlate(ReviewRound)
        .scalar_subquery()
    )
    return int(
        db.scalar(
            select(func.count(func.distinct(ReviewRound.case_id))).where(
                ReviewRound.status != RoundStatus.DECIDED,
                verdict_sah < slots,
            )
        )
        or 0
    )


def _needs_reassignment():
    """Syarat round yang perlu penugasan ulang (AC8), dipakai antrean Admin dan dashboard.

    Round belum diputuskan yang masih menunggu reviewer atau punya assignment void.
    """
    void_rounds = select(ReviewAssignment.review_round_id).where(
        ReviewAssignment.status == AssignmentStatus.VOID
    )
    return and_(
        ReviewRound.decision.is_(None),
        ReviewRound.status != RoundStatus.DECIDED,
        or_(
            ReviewRound.status == RoundStatus.AWAITING_ASSIGNMENT,
            ReviewRound.id.in_(void_rounds),
        ),
    )


def count_cases_needing_reassignment(db: Session) -> int:
    """Jumlah kasus di antrean "perlu penugasan ulang" Admin (AC8), jadi angkanya cocok."""
    return int(
        db.scalar(
            select(func.count(func.distinct(ReviewRound.case_id))).where(_needs_reassignment())
        )
        or 0
    )


def undecided_rounds(db: Session) -> list[ReviewRound]:
    """Round yang belum diputuskan, terlama dibuka lebih dulu (AC3, F-02).

    Round seperti ini selalu milik versi in_review, termasuk versi baru dari
    kasus yang statusnya tetap approved.
    """
    return list(
        db.scalars(
            select(ReviewRound)
            .where(ReviewRound.status != RoundStatus.DECIDED)
            .order_by(ReviewRound.opened_at.asc(), ReviewRound.round_no.asc())
        ).all()
    )


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


def _menunggu_penilaian(reviewer_id: uuid.UUID):
    """Assignment active reviewer ini tanpa verdict, di round yang belum diputuskan (AC8)."""
    return and_(
        ReviewAssignment.reviewer_id == reviewer_id,
        ReviewAssignment.status == AssignmentStatus.ACTIVE,
        ~ReviewAssignment.verdict.has(),
        ReviewRound.decision.is_(None),
        ReviewRound.status != RoundStatus.DECIDED,
    )


def count_cases_waiting_for_reviewer(db: Session, reviewer_id: uuid.UUID) -> int:
    """Jumlah kasus berbeda yang masih menunggu penilaian reviewer ini (AC8)."""
    return int(
        db.scalar(
            select(func.count(func.distinct(ReviewRound.case_id)))
            .select_from(ReviewAssignment)
            .join(ReviewAssignment.review_round)
            .where(_menunggu_penilaian(reviewer_id))
        )
        or 0
    )


def get_active_assignments_by_reviewer(
    db: Session, reviewer_id: uuid.UUID
) -> list[ReviewAssignment]:
    """Semua penugasan active reviewer pada round yang belum diputuskan (AC8)."""
    return list(
        db.scalars(
            select(ReviewAssignment)
            .join(ReviewAssignment.review_round)
            .options(joinedload(ReviewAssignment.review_round))
            .where(
                ReviewAssignment.reviewer_id == reviewer_id,
                ReviewAssignment.status == AssignmentStatus.ACTIVE,
                ReviewRound.decision.is_(None),
                ReviewRound.status != RoundStatus.DECIDED,
            )
        ).all()
    )


def get_assignments_for_round(db: Session, round_id: uuid.UUID) -> list[ReviewAssignment]:
    """Seluruh assignment dalam satu round, diurutkan waktu penugasan."""
    return list(
        db.scalars(
            select(ReviewAssignment)
            .where(ReviewAssignment.review_round_id == round_id)
            .order_by(ReviewAssignment.assigned_at.asc(), ReviewAssignment.id.asc())
        ).all()
    )


def get_active_assignments_for_round(db: Session, round_id: uuid.UUID) -> list[ReviewAssignment]:
    """Assignment aktif dalam satu round."""
    return list(
        db.scalars(
            select(ReviewAssignment)
            .where(
                ReviewAssignment.review_round_id == round_id,
                ReviewAssignment.status == AssignmentStatus.ACTIVE,
            )
            .order_by(ReviewAssignment.assigned_at.asc(), ReviewAssignment.id.asc())
        ).all()
    )


def get_rounds_needing_reassignment(db: Session) -> list[ReviewRound]:
    """Round aktif yang memerlukan penugasan ulang (AC8)."""
    return list(
        db.scalars(
            select(ReviewRound).where(_needs_reassignment()).order_by(ReviewRound.opened_at.asc())
        ).all()
    )


def comment_replies(db: Session, assignment_id: uuid.UUID):
    from app.modules.reviews.models import ReviewCommentReply

    return db.scalars(
        select(ReviewCommentReply)
        .where(ReviewCommentReply.assignment_id == assignment_id)
        .order_by(ReviewCommentReply.created_at, ReviewCommentReply.id)
    ).all()
