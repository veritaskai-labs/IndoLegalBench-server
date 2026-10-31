"""Aturan murni SCRUM-242: siapa boleh membaca thread komentar review.

Tanpa database. Blind rule tidak berubah: sebelum round decided tidak ada
thread yang terbuka selain untuk pemilik komentar dan Admin.
"""

import uuid

import pytest

from app.modules.reviews.models import RoundStatus
from app.modules.reviews.visibility import Viewer, can_read_discussion
from app.shared.security import Role

PEMILIK = uuid.UUID("00000000-0000-0000-0000-0000000242a1")
REKAN = uuid.UUID("00000000-0000-0000-0000-0000000242a2")
PENULIS = uuid.UUID("00000000-0000-0000-0000-0000000242b1")
ORANG_LAIN = uuid.UUID("00000000-0000-0000-0000-0000000242c1")
PESERTA = frozenset({PEMILIK, REKAN})


def _boleh(viewer: Viewer, status: RoundStatus) -> bool:
    return can_read_discussion(
        viewer,
        author_id=PENULIS,
        owner_id=PEMILIK,
        round_reviewer_ids=PESERTA,
        round_status=status,
    )


@pytest.mark.parametrize("status", list(RoundStatus))
def test_admin_membaca_kapan_pun(status):
    # Arrange
    admin = Viewer(ORANG_LAIN, Role.ADMIN)

    # Act + Assert
    assert _boleh(admin, status) is True


@pytest.mark.parametrize("status", list(RoundStatus))
def test_pemilik_komentar_membaca_threadnya_sendiri(status):
    # Arrange
    pemilik = Viewer(PEMILIK, Role.CONTRIBUTOR)

    # Act + Assert
    assert _boleh(pemilik, status) is True


def test_reviewer_lain_di_round_yang_sama_membaca_setelah_decided():
    # Arrange
    rekan = Viewer(REKAN, Role.CONTRIBUTOR)

    # Act + Assert
    assert _boleh(rekan, RoundStatus.DECIDED) is True


@pytest.mark.parametrize("status", [RoundStatus.AWAITING_ASSIGNMENT, RoundStatus.OPEN])
def test_reviewer_lain_ditolak_sebelum_decided(status):
    """Blind rule: thread rekan tertutup selama round belum diputuskan."""
    # Arrange
    rekan = Viewer(REKAN, Role.CONTRIBUTOR)

    # Act + Assert
    assert _boleh(rekan, status) is False


def test_penulis_membaca_setelah_decided_dan_ditolak_sebelumnya():
    # Arrange
    penulis = Viewer(PENULIS, Role.CONTRIBUTOR)

    # Act + Assert
    assert _boleh(penulis, RoundStatus.DECIDED) is True
    assert _boleh(penulis, RoundStatus.OPEN) is False


def test_reviewer_di_luar_round_ditolak_walau_sudah_decided():
    """Reviewer yang assignment-nya diganti atau tidak pernah ikut tidak ada di round_reviewer_ids."""
    # Arrange
    orang_luar = Viewer(ORANG_LAIN, Role.CONTRIBUTOR)

    # Act + Assert
    assert _boleh(orang_luar, RoundStatus.DECIDED) is False


def test_viewer_tanpa_peran_penulis_atau_reviewer_ditolak():
    # Arrange: peran Viewer bukan penulis dan bukan reviewer, walau user id-nya cocok
    pengamat = Viewer(PENULIS, Role.VIEWER)

    # Act + Assert
    assert _boleh(pengamat, RoundStatus.DECIDED) is False
