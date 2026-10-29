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
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.reviews import repository, service
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
from app.shared.exceptions import ConflictError
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
        penulis = _pengguna(db, Role.CONTRIBUTOR)
        reviewer = [_pengguna(db, Role.CONTRIBUTOR), _pengguna(db, Role.CONTRIBUTOR)]
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


def test_penggantian_saat_verdict_menunggu_kunci_menolak_verdict(buat_sesi):
    """Review Adra di #45. Admin mengganti reviewer (AC7) sambil memegang kunci
    round; verdict reviewer itu sudah membaca assignment-nya sebagai active,
    lalu menunggu kunci. Setelah kunci lepas, verdict harus ditolak."""
    # Arrange
    _, round_id, tugas = _round_siap_dinilai(buat_sesi)
    reviewer_id, assignment_id = next(iter(tugas.items()))
    galat: list[BaseException] = []

    def nilai():
        with buat_sesi() as db:
            try:
                service.submit_verdict(
                    db,
                    assignment_id,
                    reviewer_id=reviewer_id,
                    decision=VerdictDecision.APPROVE,
                    comment=None,
                )
            except BaseException as exc:  # noqa: BLE001 - dilaporkan ke thread utama
                galat.append(exc)

    # Act
    with buat_sesi() as admin:
        repository.lock_round(admin, round_id)
        admin.get(ReviewAssignment, assignment_id).status = AssignmentStatus.REPLACED
        admin.flush()
        utas = threading.Thread(target=nilai)
        utas.start()
        time.sleep(0.5)  # verdict sudah membaca assignment dan menunggu kunci
        admin.commit()
    utas.join(timeout=30)

    # Assert
    assert [type(exc) for exc in galat] == [ConflictError]
    assert galat[0].code == "ASSIGNMENT_NOT_ACTIVE"
    with buat_sesi() as db:
        assert db.query(ReviewVerdict).filter_by(assignment_id=assignment_id).count() == 0


def test_dua_admin_mengganti_assignment_yang_sama_bersamaan(buat_sesi):
    """Dua admin mengganti assignment yang sama secara bersamaan (AC7, SCRUM-147).

    Satu berhasil, satu lagi ditolak ConflictError (ASSIGNMENT_ALREADY_REPLACED)
    karena assignment sudah diganti setelah kunci round dilepas.
    Round tetap memiliki tepat 2 assignment aktif (D4a).
    """
    # Arrange
    _, round_id, tugas = _round_siap_dinilai(buat_sesi)
    _, assignment_id = next(iter(tugas.items()))
    with buat_sesi() as db:
        admin_1 = _pengguna(db, Role.ADMIN)
        admin_2 = _pengguna(db, Role.ADMIN)
        baru_1 = _pengguna(db, Role.CONTRIBUTOR)
        baru_2 = _pengguna(db, Role.CONTRIBUTOR)
        db.commit()

    gerbang = threading.Barrier(2)
    hasil: list[tuple[str, BaseException | None]] = []

    def ganti(admin_id, new_reviewer_id, tag):
        with buat_sesi() as db:
            gerbang.wait()
            try:
                service.replace_reviewer(
                    db,
                    assignment_id=assignment_id,
                    new_reviewer_id=new_reviewer_id,
                    reason=f"Penggantian oleh {tag}",
                    admin_id=admin_id,
                )
                hasil.append((tag, None))
            except BaseException as exc:  # noqa: BLE001 - dilaporkan ke thread utama
                hasil.append((tag, exc))

    t1 = threading.Thread(target=ganti, args=(admin_1, baru_1, "admin-1"))
    t2 = threading.Thread(target=ganti, args=(admin_2, baru_2, "admin-2"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # Assert: tepat satu sukses, satu ConflictError
    sukses = [tag for tag, exc in hasil if exc is None]
    gagal = [exc for tag, exc in hasil if exc is not None]

    assert len(sukses) == 1
    assert len(gagal) == 1
    assert isinstance(gagal[0], ConflictError)
    assert gagal[0].code == "ASSIGNMENT_ALREADY_REPLACED"

    with buat_sesi() as db:
        aktif = [
            a
            for a in repository.assignments_in_round(db, round_id)
            if a.status == AssignmentStatus.ACTIVE
        ]
        # D4a: round harus memiliki tepat 2 assignment aktif
        assert len(aktif) == 2
