"""Verdict yang masuk saat Admin melepas reviewer tetap sah. SCRUM-197 (PBI-24 AC8).

Keputusan PO 28 Okt: hanya assignment yang masih menunggu penilaian yang
di-void. Bila verdict reviewer sedang ditulis (round terkunci) saat Admin
mengonfirmasi nonaktif, verdict itu sudah masuk ketika Admin mendapat kunci,
jadi assignment-nya tidak boleh ikut di-void. SQLite mengabaikan
SELECT ... FOR UPDATE, jadi test ini harus di PostgreSQL. Data di-commit
sungguhan seperti test_review_concurrency_pg.py dan memakai helper-nya.
"""

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.auth.service import AuthService
from app.modules.reviews import repository
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
    VerdictDecision,
)
from app.shared.security import CurrentUser, Role
from tests.integration.test_review_concurrency_pg import _round_siap_dinilai

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture(scope="module")
def buat_sesi():
    engine = create_engine(POSTGRES_URL, pool_size=4)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_verdict_yang_masuk_saat_nonaktif_dikonfirmasi_tidak_di_void(buat_sesi):
    # Arrange: round open dengan dua reviewer, belum ada verdict.
    _, round_id, tugas = _round_siap_dinilai(buat_sesi)
    reviewer_id, assignment_id = next(iter(tugas.items()))
    admin = CurrentUser(uuid.uuid4(), "Admin QA", "admin-qa@veritask.test", Role.ADMIN)
    galat: list[BaseException] = []

    def nonaktifkan():
        with buat_sesi() as db:
            try:
                AuthService(db).deactivate_member(reviewer_id, admin, confirm=True)
            except BaseException as exc:  # noqa: BLE001 - dilaporkan ke thread utama
                galat.append(exc)

    # Act: transaksi verdict memegang kunci round (seperti submit_verdict) dan
    # sudah menulis verdict, lalu Admin mengonfirmasi nonaktif.
    with buat_sesi() as penilai:
        repository.lock_round(penilai, round_id)
        penilai.add(ReviewVerdict(assignment_id=assignment_id, decision=VerdictDecision.APPROVE))
        penilai.flush()
        utas = threading.Thread(target=nonaktifkan)
        utas.start()
        time.sleep(0.5)  # Admin sudah memilih assignment dan menunggu kunci round
        penilai.commit()
    utas.join(timeout=30)

    # Assert: verdict tetap sah, round tidak masuk antrean penugasan ulang.
    assert galat == []
    with buat_sesi() as db:
        tugas_dinilai = db.get(ReviewAssignment, assignment_id)
        assert tugas_dinilai.status == AssignmentStatus.ACTIVE
        assert tugas_dinilai.end_reason is None
        assert db.get(ReviewRound, round_id).status == RoundStatus.OPEN
