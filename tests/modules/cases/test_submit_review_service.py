"""Unit test cases.service.submit_for_review, dependency di-mock.

PBI-6 AC1, SCRUM-143. Alur lengkap lewat HTTP ada di test_submit_review.py.
Di sini yang diuji urutan dan transaksinya: guard dulu, lalu status
versi dan round dalam satu commit.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.cases import service
from app.modules.cases.models import CaseStatus, SplitTag
from app.shared.exceptions import ConflictError, ForbiddenError, ValidationError

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-00000000a000")
ORANG_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000b000")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
VERSI = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
ISI_LENGKAP = {
    "title": "PHK sepihak",
    "question": "Apakah PHK tanpa surat sah?",
    "legal_refs": [{"regulation_type": "UU", "regulation_number": "13", "pasal": "151"}],
    "answer_criteria": {"expected_conclusion": "tidak sah"},
}


def _kasus(*, status=CaseStatus.DRAFT, isi=None) -> SimpleNamespace:
    versi = SimpleNamespace(
        id=VERSI,
        version_no=1,
        status=status,
        split_tag=SplitTag.DEV,
        content=ISI_LENGKAP if isi is None else isi,
    )
    return SimpleNamespace(
        id=KASUS,
        suite_id=uuid.uuid4(),
        case_code="PHK-001",
        created_by=PEMBUAT,
        updated_by=PEMBUAT,
        current_version=versi,
    )


@pytest.fixture
def dep():
    kasus = _kasus()
    with (
        patch.object(service, "repository") as repo,
        patch.object(service, "reviews_service") as reviews,
        patch.object(service, "_require_active_suite") as suite,
    ):
        repo.get_by_id.return_value = kasus
        reviews.open_round.return_value = SimpleNamespace(round_no=1, status="awaiting_assignment")
        yield SimpleNamespace(repo=repo, reviews=reviews, suite=suite, kasus=kasus)


def test_versi_jadi_in_review_lalu_round_dibuka_dalam_satu_commit(dep):
    # Arrange
    db = MagicMock()

    # Act
    hasil = service.submit_for_review(db, KASUS, actor_id=PEMBUAT)

    # Assert
    assert dep.kasus.current_version.status == CaseStatus.IN_REVIEW
    dep.reviews.open_round.assert_called_once_with(db, case_id=KASUS, case_version_id=VERSI)
    dep.repo.save.assert_called_once_with(db, dep.kasus)
    assert hasil.status == CaseStatus.IN_REVIEW
    assert hasil.round_no == 1


def test_suite_diperiksa_aktif(dep):
    # Arrange
    db = MagicMock()

    # Act
    service.submit_for_review(db, KASUS, actor_id=PEMBUAT)

    # Assert
    dep.suite.assert_called_once_with(db, dep.kasus.suite_id)


def test_bukan_pembuat_ditolak_sebelum_apa_pun_berubah(dep):
    # Act
    with pytest.raises(ForbiddenError):
        service.submit_for_review(MagicMock(), KASUS, actor_id=ORANG_LAIN)

    # Assert
    assert dep.kasus.current_version.status == CaseStatus.DRAFT
    dep.reviews.open_round.assert_not_called()
    dep.repo.save.assert_not_called()


@pytest.mark.parametrize("status", [CaseStatus.IN_REVIEW, CaseStatus.APPROVED])
def test_versi_yang_tidak_terbuka_ditolak(dep, status):
    # Arrange
    dep.kasus.current_version.status = status

    # Act
    with pytest.raises(ConflictError) as info:
        service.submit_for_review(MagicMock(), KASUS, actor_id=PEMBUAT)

    # Assert
    assert info.value.code == "VERSION_NOT_SUBMITTABLE"
    dep.reviews.open_round.assert_not_called()


def test_versi_needs_revision_boleh_diajukan_ulang(dep):
    # Arrange
    dep.kasus.current_version.status = CaseStatus.NEEDS_REVISION

    # Act
    service.submit_for_review(MagicMock(), KASUS, actor_id=PEMBUAT)

    # Assert
    assert dep.kasus.current_version.status == CaseStatus.IN_REVIEW


def test_belum_lengkap_ditolak_dengan_daftar_kekurangan(dep):
    # Arrange
    dep.kasus.current_version.content = {**ISI_LENGKAP, "answer_criteria": {}}

    # Act
    with pytest.raises(ValidationError) as info:
        service.submit_for_review(MagicMock(), KASUS, actor_id=PEMBUAT)

    # Assert
    assert info.value.code == "CASE_NOT_READY"
    assert [item["field"] for item in info.value.details["missing"]] == ["answer_criteria"]
    assert dep.kasus.current_version.status == CaseStatus.DRAFT
    dep.repo.save.assert_not_called()


def test_bentrok_nomor_round_dibatalkan_jadi_konflik(dep):
    """Dua pengajuan bersamaan: yang kalah kena UQ (case_version_id, round_no)."""
    # Arrange
    db = MagicMock()
    dep.reviews.open_round.side_effect = IntegrityError("insert", {}, Exception("uq"))

    # Act
    with pytest.raises(ConflictError) as info:
        service.submit_for_review(db, KASUS, actor_id=PEMBUAT)

    # Assert
    assert info.value.code == "VERSION_NOT_SUBMITTABLE"
    db.rollback.assert_called_once()
    dep.repo.save.assert_not_called()
