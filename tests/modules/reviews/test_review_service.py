"""Unit test reviews.service, repository di-mock.

PBI-6, SCRUM-143. open_round dipanggil cases.service saat Author
mengajukan kasus. assigned_case_ids dipakai audit (SCRUM-167) untuk
membatasi Reviewer ke kasus yang pernah ditugaskan kepadanya.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.modules.reviews import service
from app.modules.reviews.models import ReviewRound, RoundStatus

KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
REVIEWER = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


@pytest.fixture
def repo():
    with patch.object(service, "repository") as palsu:
        palsu.max_round_no.return_value = 0
        yield palsu


def test_pengajuan_pertama_membuka_round_satu(repo):
    # Arrange
    db = MagicMock()

    # Act
    ronde = service.open_round(db, case_id=KASUS, case_version_id=VERSI)

    # Assert
    assert isinstance(ronde, ReviewRound)
    assert ronde.round_no == 1
    assert ronde.case_id == KASUS
    assert ronde.case_version_id == VERSI
    repo.max_round_no.assert_called_once_with(db, VERSI)
    repo.add_round.assert_called_once_with(db, ronde)


def test_pengajuan_ulang_memakai_nomor_berikutnya(repo):
    """D3 langkah 44: setelah revisi, versi yang sama masuk round 2."""
    # Arrange
    repo.max_round_no.return_value = 1

    # Act
    ronde = service.open_round(MagicMock(), case_id=KASUS, case_version_id=VERSI)

    # Assert
    assert ronde.round_no == 2


def test_round_baru_menunggu_penugasan(repo):
    """Penugasan otomatis milik SCRUM-144, jadi round belum dibuka."""
    # Act
    ronde = service.open_round(MagicMock(), case_id=KASUS, case_version_id=VERSI)

    # Assert
    assert ronde.status == RoundStatus.AWAITING_ASSIGNMENT


def test_open_round_tidak_commit(repo):
    """Commit milik pemanggil, supaya status versi dan round batal bersama."""
    # Arrange
    db = MagicMock()

    # Act
    service.open_round(db, case_id=KASUS, case_version_id=VERSI)

    # Assert
    db.commit.assert_not_called()


def test_kasus_yang_ditugaskan_dikembalikan_sebagai_frozenset(repo):
    # Arrange
    lain = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
    repo.case_ids_assigned_to.return_value = [KASUS, lain, KASUS]
    db = MagicMock()

    # Act
    hasil = service.assigned_case_ids(db, REVIEWER)

    # Assert
    assert hasil == frozenset({KASUS, lain})
    repo.case_ids_assigned_to.assert_called_once_with(db, REVIEWER)


def test_reviewer_tanpa_tugas_mendapat_himpunan_kosong(repo):
    # Arrange
    repo.case_ids_assigned_to.return_value = []

    # Act
    hasil = service.assigned_case_ids(MagicMock(), REVIEWER)

    # Assert
    assert hasil == frozenset()
