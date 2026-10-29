"""Tabel izin peran Kontributor. PBI-24 AC1 dan AC2, SCRUM-195.

Wewenang Author dan Reviewer lama digabung ke satu peran Kontributor.
Admin, Viewer, dan Developer AI tidak berubah.
"""

import pytest

from app.shared.security import REVIEWER_ROLES, WRITER_ROLES, Role


def test_kontributor_memegang_wewenang_menulis_dan_mereview():
    # Act + Assert
    assert Role.CONTRIBUTOR in WRITER_ROLES
    assert Role.CONTRIBUTOR in REVIEWER_ROLES


def test_kode_enum_kontributor_sesuai_d10():
    # Act + Assert
    assert Role.CONTRIBUTOR.value == "contributor"


def test_author_dan_reviewer_tidak_ada_lagi():
    """AC1: satu akun tetap satu peran, dari empat peran D10."""
    # Act + Assert
    assert {role.value for role in Role} == {"admin", "contributor", "viewer", "developer_ai"}


@pytest.mark.parametrize("role", [Role.ADMIN, Role.VIEWER, Role.DEVELOPER_AI])
def test_peran_lain_tidak_mendapat_wewenang_kontributor(role):
    """Admin menulis lewat izinnya sendiri di router, dan tidak pernah mereview (D10)."""
    # Act + Assert
    assert role not in WRITER_ROLES
    assert role not in REVIEWER_ROLES
