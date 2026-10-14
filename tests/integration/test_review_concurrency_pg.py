"""Dua verdict yang masuk hampir bersamaan diputuskan tepat sekali. SCRUM-145.

PBI-6 AC6 dan ticket SCRUM-145: "dihitung sekali walaupun penilaian masuk
hampir bersamaan". Tanpa kunci round, kedua transaksi menghitung satu
verdict saja dan round tidak pernah diputuskan. SQLite mengabaikan
SELECT ... FOR UPDATE, jadi test ini harus di PostgreSQL.

Data di-commit sungguhan karena dua transaksi harus saling melihat. Tidak
dibersihkan: audit_logs append-only dan merujuk pengguna yang dibuat di
sini. Setiap putaran memakai id dan email baru, dan database CI dibuang
setelah job selesai. Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.reviews import service
from app.modules.reviews.models import (
    ReviewAssignment,
    ReviewRound,
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

PUTARAN = 5


@pytest.fixture(scope="module")
def buat_sesi():
    engine = create_engine(POSTGRES_URL, pool_size=4)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _pengguna(db: Session, role: Role) -> uuid.UUID:
    user = User(email=f"{role}-{uuid.uuid4().hex}@veritask.test", name=f"QA {role}", role=role)
    db.add(user)
    db.flush()
    return user.id


def _round_siap_dinilai(buat_sesi) -> tuple[uuid.UUID, uuid.UUID, dict[uuid.UUID, uuid.UUID]]:
    """Kasus in_review dengan round open dan dua assignment aktif, sudah di-commit."""
    with buat_sesi() as db:
        penulis = _pengguna(db, Role.AUTHOR)
        reviewer = [_pengguna(db, Role.REVIEWER), _pengguna(db, Role.REVIEWER)]
        suite = Suite(name=f"QA concurrency {uuid.uuid4().hex}", created_by=penulis)
        db.add(suite)
        db.flush()
        case_id, version_id = uuid.uuid4(), uuid.uuid4()
        kode = f"QA-{uuid.uuid4().hex[:8]}"
        db.add(
            Case(
                id=case_id,
                case_code=kode,
                suite_id=suite.id,
                current_version_id=version_id,
                created_by=penulis,
                updated_by=penulis,
            )
        )
        db.add(
            CaseVersion(
                id=version_id,
                case_id=case_id,
                version_no=1,
                status=CaseStatus.IN_REVIEW,
                case_code=kode,
                split_tag=SplitTag.DEV,
                content={"title": "PHK sepihak", "question": "Sah?"},
                created_by=penulis,
            )
        )
        db.flush()
        ronde = ReviewRound(
            case_id=case_id, case_version_id=version_id, round_no=1, status=RoundStatus.OPEN
        )
        db.add(ronde)
        db.flush()
        tugas = {r: ReviewAssignment(review_round_id=ronde.id, reviewer_id=r) for r in reviewer}
        db.add_all(tugas.values())
        db.commit()
        return case_id, ronde.id, {r: t.id for r, t in tugas.items()}


def _nilai_bersamaan(buat_sesi, tugas: dict[uuid.UUID, uuid.UUID], keputusan) -> list:
    """Jalankan dua submit_verdict di dua koneksi, dilepas pada saat yang sama."""
    gerbang = threading.Barrier(len(tugas))
    galat: list[BaseException] = []

    def nilai(reviewer_id, assignment_id, decision):
        with buat_sesi() as db:
            gerbang.wait()
            try:
                service.submit_verdict(
                    db,
                    assignment_id,
                    reviewer_id=reviewer_id,
                    decision=decision,
                    comment="Perlu diperbaiki" if decision == VerdictDecision.REVISE else None,
                )
            except BaseException as exc:  # noqa: BLE001 - dilaporkan ke thread utama
                galat.append(exc)

    utas = [
        threading.Thread(target=nilai, args=(r, a, d))
        for (r, a), d in zip(tugas.items(), keputusan, strict=True)
    ]
    for t in utas:
        t.start()
    for t in utas:
        t.join(timeout=30)
    return galat


@pytest.mark.parametrize(
    ("keputusan", "hasil", "status_versi"),
    [
        ((VerdictDecision.APPROVE, VerdictDecision.APPROVE), RoundDecision.APPROVED, "approved"),
        ((VerdictDecision.APPROVE, VerdictDecision.REVISE), RoundDecision.REVISE, "needs_revision"),
    ],
    ids=["dua-approve", "approve-revise"],
)
def test_round_diputuskan_tepat_sekali(buat_sesi, keputusan, hasil, status_versi):
    for _ in range(PUTARAN):
        # Arrange
        case_id, round_id, tugas = _round_siap_dinilai(buat_sesi)

        # Act
        galat = _nilai_bersamaan(buat_sesi, tugas, keputusan)

        # Assert
        assert galat == []
        with buat_sesi() as db:
            ronde = db.get(ReviewRound, round_id)
            assert (ronde.status, ronde.decision) == (RoundStatus.DECIDED, hasil)
            assert db.get(Case, case_id).current_version.status.value == status_versi
            aksi = f"case.{'approved' if hasil == RoundDecision.APPROVED else 'revision_requested'}"
            assert db.query(AuditLog).filter_by(case_id=case_id, action=aksi).count() == 1
            assert (
                db.query(AuditLog)
                .filter_by(case_id=case_id, action="review.verdict_submitted")
                .count()
                == 2
            )
