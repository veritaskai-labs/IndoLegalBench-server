"""Query penilaian di reviews.repository terhadap database uji.

PBI-6, SCRUM-145. Yang diuji query-nya sendiri, jadi memakai SQLite
in-memory, bukan mock.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.modules.reviews import repository
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
    VerdictDecision,
)

KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
KASUS_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
REVIEWER_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REVIEWER_B = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
AWAL = datetime(2026, 10, 1, tzinfo=UTC)


def _round(db_session, *, case_id=KASUS, round_no=1, status=RoundStatus.OPEN, opened_at=AWAL):
    ronde = ReviewRound(
        case_id=case_id,
        case_version_id=uuid.uuid4(),
        round_no=round_no,
        status=status,
        opened_at=opened_at,
    )
    db_session.add(ronde)
    db_session.flush()
    return ronde


def _tugas(db_session, ronde, reviewer_id, status=AssignmentStatus.ACTIVE) -> ReviewAssignment:
    tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=reviewer_id, status=status)
    db_session.add(tugas)
    db_session.flush()
    return tugas


def _nilai(db_session, tugas, decision=VerdictDecision.APPROVE, comment=None) -> ReviewVerdict:
    verdict = ReviewVerdict(assignment_id=tugas.id, decision=decision, comment=comment)
    db_session.add(verdict)
    db_session.flush()
    return verdict


def test_assignment_dimuat_bersama_round_dan_verdict(db_session):
    # Arrange
    ronde = _round(db_session)
    tugas = _tugas(db_session, ronde, REVIEWER_A)
    _nilai(db_session, tugas)
    db_session.expire_all()

    # Act
    hasil = repository.get_assignment(db_session, tugas.id)

    # Assert
    assert hasil.review_round.case_id == KASUS
    assert hasil.verdict.decision == VerdictDecision.APPROVE


def test_assignment_segar_membaca_ulang_dari_database(db_session):
    """Tanpa fresh, identity map mengembalikan status lama yang sudah dimuat."""
    # Arrange
    tugas = _tugas(db_session, _round(db_session), REVIEWER_A)
    db_session.commit()
    repository.get_assignment(db_session, tugas.id)
    db_session.execute(
        ReviewAssignment.__table__.update()
        .where(ReviewAssignment.__table__.c.id == tugas.id)
        .values(status="replaced")
    )

    # Act
    lama = repository.get_assignment(db_session, tugas.id).status
    segar = repository.get_assignment(db_session, tugas.id, fresh=True).status

    # Assert
    assert lama == AssignmentStatus.ACTIVE
    assert segar == AssignmentStatus.REPLACED


def test_assignment_tidak_dikenal(db_session):
    # Act + Assert
    assert repository.get_assignment(db_session, uuid.uuid4()) is None


def test_kunci_round_mengembalikan_baris_terbaru(db_session):
    # Arrange
    ronde = _round(db_session)

    # Act
    terkunci = repository.lock_round(db_session, ronde.id)

    # Assert
    assert terkunci.id == ronde.id


def test_isi_round_semua_assignment_dengan_verdictnya(db_session):
    # Arrange
    ronde = _round(db_session)
    sudah = _tugas(db_session, ronde, REVIEWER_A)
    _nilai(db_session, sudah, VerdictDecision.REVISE, "Pasal salah")
    belum = _tugas(db_session, ronde, REVIEWER_B)
    diganti = _tugas(db_session, ronde, REVIEWER_B, AssignmentStatus.REPLACED)
    _tugas(db_session, _round(db_session, case_id=KASUS_LAIN), REVIEWER_A)

    # Act
    hasil = repository.assignments_in_round(db_session, ronde.id)

    # Assert
    assert {tugas.id for tugas in hasil} == {sudah.id, belum.id, diganti.id}
    verdict = {tugas.id: tugas.verdict for tugas in hasil}
    assert verdict[sudah.id].comment == "Pasal salah"
    assert verdict[belum.id] is None


def test_tugas_terbuka_hanya_yang_aktif_di_round_open(db_session):
    # Arrange
    terbuka = _tugas(db_session, _round(db_session), REVIEWER_A)
    _tugas(db_session, _round(db_session, round_no=2), REVIEWER_A, AssignmentStatus.VOID)
    _tugas(db_session, _round(db_session, status=RoundStatus.DECIDED, round_no=3), REVIEWER_A)
    _tugas(db_session, _round(db_session, case_id=KASUS_LAIN), REVIEWER_B)

    # Act
    hasil = repository.open_assignments_for(db_session, REVIEWER_A)

    # Assert
    assert [tugas.id for tugas in hasil] == [terbuka.id]


def test_tugas_terbuka_terlama_lebih_dulu(db_session):
    # Arrange
    baru = _tugas(db_session, _round(db_session, opened_at=AWAL + timedelta(days=1)), REVIEWER_A)
    lama = _tugas(db_session, _round(db_session, case_id=KASUS_LAIN), REVIEWER_A)

    # Act
    hasil = repository.open_assignments_for(db_session, REVIEWER_A)

    # Assert
    assert [tugas.id for tugas in hasil] == [lama.id, baru.id]


def test_round_terakhir_suatu_kasus(db_session):
    # Arrange
    _round(db_session, round_no=1)
    terakhir = _round(db_session, round_no=2, opened_at=AWAL + timedelta(days=2))
    _round(db_session, case_id=KASUS_LAIN, opened_at=AWAL + timedelta(days=9))

    # Act
    hasil = repository.latest_round_for_case(db_session, KASUS)

    # Assert
    assert hasil.id == terakhir.id


def test_kasus_tanpa_round(db_session):
    # Act + Assert
    assert repository.latest_round_for_case(db_session, KASUS) is None


def test_add_verdict_hanya_flush(db_session):
    # Arrange
    tugas = _tugas(db_session, _round(db_session), REVIEWER_A)

    # Act
    repository.add_verdict(
        db_session, ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE)
    )
    db_session.rollback()

    # Assert
    assert db_session.query(ReviewVerdict).count() == 0
