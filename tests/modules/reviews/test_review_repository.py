"""Query reviews.repository terhadap database uji.

PBI-6, SCRUM-143/144. Yang diuji di sini adalah query-nya sendiri, jadi
memakai SQLite in-memory, bukan mock.
"""

import uuid

from app.modules.reviews import repository
from app.modules.reviews.models import (
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    VerdictDecision,
)

KASUS_1 = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
KASUS_2 = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
KASUS_3 = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
VERSI_1 = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
REVIEWER = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
REVIEWER_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


def _round(db_session, case_id: uuid.UUID, *, round_no: int = 1) -> ReviewRound:
    ronde = ReviewRound(case_id=case_id, case_version_id=uuid.uuid4(), round_no=round_no)
    db_session.add(ronde)
    db_session.flush()
    return ronde


def _tugaskan(db_session, ronde: ReviewRound, reviewer_id: uuid.UUID, status=None) -> None:
    db_session.add(
        ReviewAssignment(
            review_round_id=ronde.id,
            reviewer_id=reviewer_id,
            status=status or AssignmentStatus.ACTIVE,
        )
    )
    db_session.flush()


def test_nomor_round_tertinggi_nol_bila_belum_pernah_diajukan(db_session):
    # Act + Assert
    assert repository.max_round_no(db_session, VERSI_1) == 0


def test_nomor_round_tertinggi_per_versi(db_session):
    # Arrange
    for nomor in (1, 2):
        db_session.add(ReviewRound(case_id=KASUS_1, case_version_id=VERSI_1, round_no=nomor))
    db_session.add(ReviewRound(case_id=KASUS_2, case_version_id=uuid.uuid4(), round_no=5))
    db_session.flush()

    # Act + Assert
    assert repository.max_round_no(db_session, VERSI_1) == 2


def test_add_round_hanya_flush(db_session):
    # Arrange
    ronde = ReviewRound(case_id=KASUS_1, case_version_id=VERSI_1, round_no=1)

    # Act
    repository.add_round(db_session, ronde)
    db_session.rollback()

    # Assert
    assert db_session.query(ReviewRound).count() == 0


def test_kasus_yang_pernah_ditugaskan_termasuk_yang_sudah_diganti(db_session):
    """Asumsi #10 (D5b): Reviewer melihat kasus yang PERNAH ditugaskan."""
    # Arrange
    _tugaskan(db_session, _round(db_session, KASUS_1), REVIEWER)
    _tugaskan(db_session, _round(db_session, KASUS_2), REVIEWER, AssignmentStatus.REPLACED)
    _tugaskan(db_session, _round(db_session, KASUS_3), REVIEWER, AssignmentStatus.VOID)

    # Act
    hasil = set(repository.case_ids_assigned_to(db_session, REVIEWER))

    # Assert
    assert hasil == {KASUS_1, KASUS_2, KASUS_3}


def test_kasus_milik_reviewer_lain_tidak_ikut(db_session):
    # Arrange
    _tugaskan(db_session, _round(db_session, KASUS_1), REVIEWER)
    _tugaskan(db_session, _round(db_session, KASUS_2), REVIEWER_LAIN)

    # Act
    hasil = list(repository.case_ids_assigned_to(db_session, REVIEWER))

    # Assert
    assert hasil == [KASUS_1]


def test_kasus_yang_direview_di_beberapa_round_muncul_sekali(db_session):
    # Arrange
    _tugaskan(db_session, _round(db_session, KASUS_1, round_no=1), REVIEWER)
    _tugaskan(db_session, _round(db_session, KASUS_1, round_no=2), REVIEWER)

    # Act
    hasil = list(repository.case_ids_assigned_to(db_session, REVIEWER))

    # Assert
    assert hasil == [KASUS_1]


def test_get_round_mengembalikan_ronde_atau_none(db_session):
    # Arrange
    ronde = _round(db_session, KASUS_1)

    # Act + Assert
    assert repository.get_round(db_session, ronde.id) is ronde
    assert repository.get_round(db_session, uuid.uuid4()) is None


def test_reviewer_assignments_menandai_yang_sudah_punya_verdict(db_session):
    # Arrange
    ronde = _round(db_session, KASUS_1)
    tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=REVIEWER)
    db_session.add(tugas)
    db_session.flush()
    db_session.add(ReviewVerdict(assignment_id=tugas.id, decision=VerdictDecision.APPROVE))
    db_session.flush()
    _tugaskan(db_session, _round(db_session, KASUS_2), REVIEWER)

    # Act
    hasil = list(repository.reviewer_assignments(db_session, [REVIEWER]))

    # Assert
    assert len(hasil) == 2
    by_round = {tugas.review_round_id: punya for tugas, punya in hasil}
    assert by_round[ronde.id] is True
    assert False in by_round.values()


def test_add_assignment_tidak_flush(db_session):
    # Arrange
    ronde = _round(db_session, KASUS_1)
    tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=REVIEWER)

    # Act
    repository.add_assignment(db_session, tugas)

    # Assert
    assert tugas in db_session.new


def test_get_assignment_ditemukan_dan_memuat_round(db_session):
    # Arrange
    ronde = _round(db_session, KASUS_1)
    tugas = ReviewAssignment(review_round_id=ronde.id, reviewer_id=REVIEWER)
    db_session.add(tugas)
    db_session.flush()

    # Act
    hasil = repository.get_assignment(db_session, tugas.id)

    # Assert
    assert hasil is not None
    assert hasil.id == tugas.id
    assert hasil.review_round.id == ronde.id
    assert repository.get_assignment(db_session, uuid.uuid4()) is None


def test_get_active_assignments_by_reviewer_hanya_yang_aktif_dan_belum_decided(db_session):
    from app.modules.reviews.models import RoundDecision, RoundStatus

    # Arrange
    ronde1 = _round(db_session, KASUS_1)
    ronde2 = _round(db_session, KASUS_2)
    ronde3 = _round(db_session, KASUS_3)
    ronde3.status = RoundStatus.DECIDED
    ronde3.decision = RoundDecision.APPROVED
    _tugaskan(db_session, ronde1, REVIEWER, AssignmentStatus.ACTIVE)
    _tugaskan(db_session, ronde2, REVIEWER, AssignmentStatus.VOID)
    _tugaskan(db_session, ronde3, REVIEWER, AssignmentStatus.ACTIVE)

    # Act
    aktif = repository.waiting_assignments_for_reviewer(db_session, REVIEWER)

    # Assert
    assert len(aktif) == 1
    assert aktif[0].review_round_id == ronde1.id


def test_get_assignments_for_round_dan_aktif(db_session):
    # Arrange
    ronde = _round(db_session, KASUS_1)
    _tugaskan(db_session, ronde, REVIEWER, AssignmentStatus.ACTIVE)
    _tugaskan(db_session, ronde, REVIEWER_LAIN, AssignmentStatus.REPLACED)

    # Act
    semua = repository.get_assignments_for_round(db_session, ronde.id)
    aktif = repository.get_active_assignments_for_round(db_session, ronde.id)

    # Assert
    assert len(semua) == 2
    assert len(aktif) == 1
    assert aktif[0].reviewer_id == REVIEWER


def test_get_rounds_needing_reassignment(db_session):
    from app.modules.reviews.models import RoundDecision, RoundStatus

    # Ronde 1: awaiting_assignment -> harus muncul
    ronde1 = _round(db_session, KASUS_1)
    ronde1.status = RoundStatus.AWAITING_ASSIGNMENT
    db_session.flush()

    # Ronde 2: open tapi ada assignment void -> harus muncul
    ronde2 = _round(db_session, KASUS_2)
    ronde2.status = RoundStatus.OPEN
    _tugaskan(db_session, ronde2, REVIEWER, AssignmentStatus.VOID)

    # Ronde 3: open dengan assignment active saja -> TIDAK muncul
    ronde3 = _round(db_session, KASUS_3)
    ronde3.status = RoundStatus.OPEN
    _tugaskan(db_session, ronde3, REVIEWER, AssignmentStatus.ACTIVE)

    # Ronde 4: decided -> TIDAK muncul
    ronde4 = _round(db_session, KASUS_1, round_no=2)
    ronde4.status = RoundStatus.DECIDED
    ronde4.decision = RoundDecision.APPROVED
    _tugaskan(db_session, ronde4, REVIEWER, AssignmentStatus.VOID)

    # Act
    antrean = repository.get_rounds_needing_reassignment(db_session)

    # Assert
    ids = [r.id for r in antrean]
    assert ronde1.id in ids
    assert ronde2.id in ids
    assert ronde3.id not in ids
    assert ronde4.id not in ids


def test_reviewer_assignments_tanpa_reviewer_tidak_query(db_session):
    assert repository.reviewer_assignments(db_session, []) == []
