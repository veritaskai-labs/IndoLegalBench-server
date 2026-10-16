"""Unit test cases.service.change_split_tag, dependency di-mock.

PBI-9 AC3, SCRUM-164. Setelah disetujui, hanya Admin yang mengubah tag
(dijaga router), perubahan dev ke test minta konfirmasi, dan penanda
"pernah dev" tidak pernah hilang. Alur lewat HTTP ada di
test_split_tag_api.py.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases import service
from app.modules.cases.models import CaseStatus, SplitTag
from app.shared.exceptions import ConflictError, ValidationError

ADMIN = uuid.UUID("00000000-0000-0000-0000-00000000c000")
KASUS = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def _versi(tag=SplitTag.DEV, status=CaseStatus.APPROVED):
    return SimpleNamespace(id=uuid.uuid4(), split_tag=tag, status=status)


@pytest.fixture
def dep():
    disetujui = _versi()
    kasus = SimpleNamespace(
        id=KASUS,
        suite_id=uuid.uuid4(),
        was_dev=False,
        updated_by=None,
        latest_approved_version=disetujui,
        current_version=disetujui,
    )
    with (
        patch.object(service, "repository") as repo,
        patch.object(service, "_require_active_suite") as suite,
        patch.object(service, "_to_read") as baca,
    ):
        repo.get_by_id.return_value = kasus
        yield SimpleNamespace(repo=repo, suite=suite, baca=baca, kasus=kasus, disetujui=disetujui)


def _ubah(tag, *, confirm=False, db=None):
    return service.change_split_tag(db or MagicMock(), KASUS, tag, actor_id=ADMIN, confirm=confirm)


def test_dev_ke_test_tanpa_konfirmasi_ditolak_dan_tidak_berubah(dep):
    # Act
    with pytest.raises(ConflictError) as info:
        _ubah(SplitTag.TEST)

    # Assert
    assert info.value.code == "TAG_CHANGE_NEEDS_CONFIRMATION"
    assert info.value.details == {"requires_confirmation": True}
    assert dep.disetujui.split_tag == SplitTag.DEV
    assert dep.kasus.was_dev is False
    dep.repo.save.assert_not_called()


def test_dev_ke_test_dengan_konfirmasi_menandai_pernah_dev(dep):
    # Arrange
    db = MagicMock()

    # Act
    _ubah(SplitTag.TEST, confirm=True, db=db)

    # Assert
    assert dep.disetujui.split_tag == SplitTag.TEST
    assert dep.kasus.was_dev is True
    assert dep.kasus.updated_by == ADMIN
    dep.repo.save.assert_called_once_with(db, dep.kasus)
    dep.baca.assert_called_once_with(dep.kasus)


def test_test_ke_dev_tidak_perlu_konfirmasi(dep):
    # Arrange
    dep.disetujui.split_tag = SplitTag.TEST

    # Act
    _ubah(SplitTag.DEV)

    # Assert
    assert dep.disetujui.split_tag == SplitTag.DEV
    assert dep.kasus.was_dev is False


def test_penanda_pernah_dev_tidak_hilang_saat_kembali_ke_dev(dep):
    # Arrange
    dep.disetujui.split_tag = SplitTag.TEST
    dep.kasus.was_dev = True

    # Act
    _ubah(SplitTag.DEV)

    # Assert
    assert dep.kasus.was_dev is True


@pytest.mark.parametrize(
    "status", [CaseStatus.DRAFT, CaseStatus.IN_REVIEW, CaseStatus.NEEDS_REVISION]
)
def test_versi_terbuka_ikut_berubah(dep, status):
    """Tanpa ini, menyetujui versi berikutnya membawa kembali tag lama."""
    # Arrange
    terbuka = _versi(SplitTag.DEV, status)
    dep.kasus.current_version = terbuka

    # Act
    _ubah(SplitTag.TEST, confirm=True)

    # Assert
    assert terbuka.split_tag == SplitTag.TEST
    assert dep.disetujui.split_tag == SplitTag.TEST


def test_tag_sama_tidak_menyimpan_apa_pun(dep):
    # Act
    _ubah(SplitTag.DEV)

    # Assert
    dep.repo.save.assert_not_called()
    dep.baca.assert_called_once_with(dep.kasus)


def test_kasus_yang_belum_disetujui_diubah_lewat_put(dep):
    """Sebelum disetujui, tag milik penulis dan diubah lewat PUT /cases/{id}."""
    # Arrange
    dep.kasus.latest_approved_version = None

    # Act
    with pytest.raises(ConflictError) as info:
        _ubah(SplitTag.TEST, confirm=True)

    # Assert
    assert info.value.code == "NO_APPROVED_VERSION"
    dep.repo.save.assert_not_called()


def test_suite_tidak_aktif_ditolak(dep):
    """D2: selama suite diarsipkan, semua aksi edit dibekukan."""
    # Arrange
    dep.suite.side_effect = ValidationError("arsip", code="SUITE_NOT_ACTIVE")

    # Act
    with pytest.raises(ValidationError):
        _ubah(SplitTag.TEST, confirm=True)

    # Assert
    assert dep.disetujui.split_tag == SplitTag.DEV
