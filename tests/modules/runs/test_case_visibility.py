"""Aturan siapa boleh melihat isi kasus sesi pengukuran. PBI-11 AC9, SCRUM-261.

Fungsi murni, tanpa database. Developer AI hanya melihat kasus dev; semua
yang lain (test, tag kosong, tag tak dikenal) tertutup (fail-closed).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: isi yang tidak boleh terlihat.
- Edge: batas atau kasus pinggir.
"""

import pytest

from app.modules.runs.case_visibility import can_view_case_content
from app.shared.security import Role


@pytest.mark.parametrize("split_tag", ["dev", "test"])
def test_admin_melihat_kasus_dev_dan_test(split_tag):
    """Positif: Admin melihat isi semua kasus."""
    # Act
    visible = can_view_case_content(Role.ADMIN, split_tag)

    # Assert
    assert visible is True


def test_developer_ai_melihat_kasus_dev():
    """Positif: Developer AI melihat pertanyaan, jawaban, dan detail kasus dev."""
    # Act
    visible = can_view_case_content(Role.DEVELOPER_AI, "dev")

    # Assert
    assert visible is True


def test_developer_ai_tidak_melihat_kasus_test():
    """Negatif: isi kasus test tertutup untuk Developer AI."""
    # Act
    visible = can_view_case_content(Role.DEVELOPER_AI, "test")

    # Assert
    assert visible is False


@pytest.mark.parametrize("split_tag", [None, "", "Dev", "both", "staging"])
def test_developer_ai_tidak_melihat_tag_kosong_atau_tak_dikenal(split_tag):
    """Edge: fail-closed. Hanya tag persis "dev" yang terbuka, huruf besar pun tidak."""
    # Act
    visible = can_view_case_content(Role.DEVELOPER_AI, split_tag)

    # Assert
    assert visible is False


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
@pytest.mark.parametrize("split_tag", ["dev", "test", None])
def test_peran_lain_tidak_melihat_isi_kasus_sesi(role, split_tag):
    """Negatif: peran di luar Admin dan Developer AI tidak punya akses ke isi kasus sesi."""
    # Act
    visible = can_view_case_content(role, split_tag)

    # Assert
    assert visible is False
