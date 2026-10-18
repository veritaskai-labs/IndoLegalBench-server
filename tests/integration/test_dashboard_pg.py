"""Angka dashboard dihitung benar di PostgreSQL. PBI-19, SCRUM-154.

Test API dashboard berjalan di SQLite. Query agregatnya (COUNT DISTINCT,
COALESCE versi yang berlaku, subquery assignment void, filter penugasan
ulang yang dipakai bersama antrean Admin) perlu dibuktikan sekali di
PostgreSQL 16 yang sama dengan staging.

Database CI dipakai bersama test _pg lain yang meng-commit datanya, jadi
yang diperiksa adalah selisih sebelum dan sesudah, dalam satu transaksi
yang di-rollback. Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.dashboard import service as dashboard_service
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)
from app.modules.suites.models import Suite
from app.shared.security import Role

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

AWAL = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine(POSTGRES_URL)
    with sessionmaker(bind=engine)() as sesi:
        yield sesi
        sesi.rollback()
    engine.dispose()


def _pengguna(db: Session, role: Role) -> uuid.UUID:
    user = User(email=f"{role}-{uuid.uuid4().hex}@veritask.test", name=f"QA {role}", role=role)
    db.add(user)
    db.flush()
    return user.id


def _kasus(
    db: Session, penulis: uuid.UUID, suite_id: uuid.UUID, status: CaseStatus
) -> tuple[uuid.UUID, uuid.UUID, str]:
    case_id, version_id = uuid.uuid4(), uuid.uuid4()
    kode = f"QA-DASH-{uuid.uuid4().hex[:8]}"
    db.add(
        Case(
            id=case_id,
            case_code=kode,
            suite_id=suite_id,
            current_version_id=version_id,
            latest_approved_version_id=version_id if status == CaseStatus.APPROVED else None,
            created_by=penulis,
            updated_by=penulis,
        )
    )
    db.add(
        CaseVersion(
            id=version_id,
            case_id=case_id,
            version_no=1,
            status=status,
            case_code=kode,
            split_tag=SplitTag.DEV,
            content={"title": f"Judul {kode}", "question": "Sah?"},
            created_by=penulis,
        )
    )
    db.flush()
    return case_id, version_id, kode


def _round(
    db: Session,
    case_id: uuid.UUID,
    version_id: uuid.UUID,
    status: RoundStatus,
    menit: int,
    slot: list[tuple[uuid.UUID, AssignmentStatus, VerdictDecision | None]],
) -> None:
    """Satu round dengan assignment dan verdict-nya. menit = urutan dibuka."""
    decided = status == RoundStatus.DECIDED
    ronde = ReviewRound(
        case_id=case_id,
        case_version_id=version_id,
        round_no=1,
        status=status,
        decision=RoundDecision.APPROVED if decided else None,
        opened_at=AWAL + timedelta(minutes=menit),
        decided_at=AWAL + timedelta(minutes=menit + 1) if decided else None,
    )
    db.add(ronde)
    db.flush()
    for reviewer_id, status_tugas, keputusan in slot:
        tugas = ReviewAssignment(
            review_round_id=ronde.id, reviewer_id=reviewer_id, status=status_tugas
        )
        db.add(tugas)
        db.flush()
        if keputusan is not None:
            db.add(ReviewVerdict(assignment_id=tugas.id, decision=keputusan))
    db.flush()


def _ringkasan(db: Session, admin: uuid.UUID, reviewer: uuid.UUID):
    return (
        dashboard_service.summary(db, user_id=admin, role=Role.ADMIN),
        dashboard_service.summary(db, user_id=reviewer, role=Role.REVIEWER),
    )


def test_angka_dashboard_sesuai_aturan_di_postgres(db):
    # Arrange
    penulis = _pengguna(db, Role.AUTHOR)
    admin = _pengguna(db, Role.ADMIN)
    saya = _pengguna(db, Role.REVIEWER)
    lain = _pengguna(db, Role.REVIEWER)
    lama = _pengguna(db, Role.REVIEWER)
    suite = Suite(name=f"QA dashboard {uuid.uuid4().hex}", created_by=penulis)
    db.add(suite)
    db.flush()
    admin_awal, reviewer_awal = _ringkasan(db, admin, saya)
    assert reviewer_awal.review_queue == []

    def kasus(status: CaseStatus) -> tuple[uuid.UUID, uuid.UUID, str]:
        return _kasus(db, penulis, suite.id, status)

    _ACTIVE, _VOID, _REPLACED = (
        AssignmentStatus.ACTIVE,
        AssignmentStatus.VOID,
        AssignmentStatus.REPLACED,
    )
    _OK = VerdictDecision.APPROVE

    kasus(CaseStatus.DRAFT)
    c, v, _ = kasus(CaseStatus.APPROVED)
    _round(db, c, v, RoundStatus.DECIDED, 0, [(saya, _ACTIVE, _OK), (lain, _ACTIVE, _OK)])
    # Belum ada yang menilai: menunggu, masuk antrean saya.
    c, v, belum_dinilai = kasus(CaseStatus.IN_REVIEW)
    _round(db, c, v, RoundStatus.OPEN, 10, [(saya, _ACTIVE, None), (lain, _ACTIVE, None)])
    # Saya sudah menilai, yang lain belum: menunggu, tidak di antrean saya.
    c, v, _ = kasus(CaseStatus.IN_REVIEW)
    _round(db, c, v, RoundStatus.OPEN, 20, [(saya, _ACTIVE, _OK), (lain, _ACTIVE, None)])
    # Baru satu reviewer: menunggu dan perlu penugasan ulang.
    c, v, _ = kasus(CaseStatus.IN_REVIEW)
    _round(db, c, v, RoundStatus.AWAITING_ASSIGNMENT, 30, [(lain, _ACTIVE, None)])
    # Reviewer dinonaktifkan setelah approve: slotnya kosong lagi (void).
    c, v, ada_void = kasus(CaseStatus.IN_REVIEW)
    _round(db, c, v, RoundStatus.OPEN, 40, [(lama, _VOID, _OK), (saya, _ACTIVE, None)])
    # Approve reviewer yang diganti tidak dihitung (PBI-6 AC7).
    c, v, ada_pengganti = kasus(CaseStatus.IN_REVIEW)
    _round(
        db,
        c,
        v,
        RoundStatus.OPEN,
        50,
        [(lama, _REPLACED, _OK), (lain, _ACTIVE, _OK), (saya, _ACTIVE, None)],
    )

    # Act
    admin_akhir, reviewer_akhir = _ringkasan(db, admin, saya)

    # Assert
    awal, akhir = admin_awal.case_counts, admin_akhir.case_counts
    assert akhir.draft - awal.draft == 1
    assert akhir.approved - awal.approved == 1
    assert akhir.in_review - awal.in_review == 5
    assert akhir.needs_revision == awal.needs_revision
    assert admin_akhir.total_cases - admin_awal.total_cases == 7
    assert admin_akhir.waiting_for_review - admin_awal.waiting_for_review == 5
    assert admin_akhir.needs_reassignment - admin_awal.needs_reassignment == 2
    assert reviewer_akhir.needs_reassignment is None
    assert [baris.case_code for baris in reviewer_akhir.review_queue] == [
        belum_dinilai,
        ada_void,
        ada_pengganti,
    ]
