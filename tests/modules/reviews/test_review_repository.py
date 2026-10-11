"""Query reviews.repository terhadap database uji.

PBI-6, SCRUM-143. Yang diuji di sini adalah query-nya sendiri, jadi
memakai SQLite in-memory, bukan mock.
"""

import uuid

from app.modules.reviews import repository
from app.modules.reviews.models import AssignmentStatus, ReviewAssignment, ReviewRound

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
