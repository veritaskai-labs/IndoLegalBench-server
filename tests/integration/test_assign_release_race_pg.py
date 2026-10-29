"""Anggota yang sedang dilepas tidak boleh tetap mendapat tugas review baru. SCRUM-197.

Follow-up #78 (PBI-24 AC8). assign_on_submit membaca reviewer aktif tanpa
kunci. Bila kasus sedang diajukan (assignment sudah ditulis, belum di-commit)
saat Admin mengonfirmasi nonaktif, Admin tidak melihat assignment itu dan
selesai lebih dulu; setelah pengajuan di-commit, anggota nonaktif memegang
assignment active yang tidak pernah masuk antrean penugasan ulang.
SQLite mengabaikan kunci baris, jadi test ini harus di PostgreSQL.
"""

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.reviews import service as reviews_service
from app.modules.reviews.models import AssignmentStatus, ReviewAssignment, ReviewRound, RoundStatus
from app.modules.reviews.ports import NoopNotifier
from app.modules.suites.models import Suite
from app.shared.security import CurrentUser, Role

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

# Satu tempat untuk peran yang bisa mereview: setelah SCRUM-195 (#76) cukup
# diganti jadi Role.CONTRIBUTOR.
PERAN_REVIEWER = Role.REVIEWER


@pytest.fixture(scope="module")
def buat_sesi():
    engine = create_engine(POSTGRES_URL, pool_size=4)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _pengguna(db: Session, role: Role) -> uuid.UUID:
    user = User(email=f"{role}-197-{uuid.uuid4().hex}@veritask.test", name=f"QA {role}", role=role)
    db.add(user)
    db.flush()
    return user.id


def _round_baru(buat_sesi) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Round 1 awaiting_assignment tanpa assignment, plus penulis dan satu-satunya
    pasangan reviewer yang memenuhi syarat (dua reviewer baru), sudah di-commit.

    Mengembalikan (round_id, penulis, reviewer_target).
    """
    with buat_sesi() as db:
        # Reviewer aktif lain dari test sebelumnya ikut jadi kandidat; nonaktifkan
        # supaya pilihan assign_on_submit pasti kedua reviewer baru ini.
        db.query(User).filter(User.role == PERAN_REVIEWER).update({"is_active": False})
        penulis = _pengguna(db, Role.AUTHOR)
        target = _pengguna(db, PERAN_REVIEWER)
        _pengguna(db, PERAN_REVIEWER)
        suite = Suite(name=f"QA 197 race {uuid.uuid4().hex}", created_by=penulis)
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
            case_id=case_id,
            case_version_id=version_id,
            round_no=1,
            status=RoundStatus.AWAITING_ASSIGNMENT,
        )
        db.add(ronde)
        db.commit()
        return ronde.id, penulis, target


def test_pengajuan_bersamaan_dengan_nonaktif_tidak_meninggalkan_tugas_aktif(buat_sesi):
    # Arrange
    round_id, penulis, target = _round_baru(buat_sesi)
    admin = CurrentUser(uuid.uuid4(), "Admin QA", "admin-qa-197@veritask.test", Role.ADMIN)
    galat: list[BaseException] = []

    def nonaktifkan():
        with buat_sesi() as db:
            try:
                AuthService(db, notifier=NoopNotifier()).deactivate_member(
                    target, admin, confirm=True
                )
            except BaseException as exc:  # noqa: BLE001 - dilaporkan ke thread utama
                galat.append(exc)

    # Act: pengajuan sudah menugaskan target tetapi belum commit, lalu Admin
    # mengonfirmasi nonaktif.
    with buat_sesi() as pengaju:
        terpilih = reviews_service.assign_on_submit(
            pengaju, round_id=round_id, author_id=penulis, notifier=NoopNotifier()
        )
        assert target in terpilih
        utas = threading.Thread(target=nonaktifkan)
        utas.start()
        time.sleep(0.5)  # Admin sudah berjalan sementara pengajuan belum commit
        pengaju.commit()
    utas.join(timeout=30)

    # Assert: anggota nonaktif tidak memegang tugas active; kasusnya ada di antrean.
    assert galat == []
    with buat_sesi() as db:
        assert db.get(User, target).is_active is False
        aktif = (
            db.query(ReviewAssignment)
            .filter_by(reviewer_id=target, status=AssignmentStatus.ACTIVE)
            .count()
        )
        assert aktif == 0
        assert db.get(ReviewRound, round_id).status == RoundStatus.AWAITING_ASSIGNMENT
