"""Aturan siapa boleh melihat isi kasus sesi pengukuran. PBI-11 AC9, SCRUM-261.

Fungsi murni, tanpa database. Developer AI hanya melihat kasus dev; semua
yang lain (test, tag kosong, tag tak dikenal) tertutup (fail-closed).

Bagian akhir menguji tabel penelusuran laporan (PBI-16 SCRUM-326, D9 Sprint 4), yang
sengaja berbeda dari tabel sesi untuk Kontributor.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: isi yang tidak boleh terlihat.
- Edge: batas atau kasus pinggir.
"""

import pytest

from app.modules.runs.case_visibility import can_view_case_content, can_view_drilldown_content
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


# --- Penelusuran angka laporan (PBI-16 SCRUM-326, D9 Sprint 4) ----------------


@pytest.mark.parametrize(
    ("role", "split_tag", "boleh"),
    [
        # Positif: Admin membuka isi kasus test dan dev.
        (Role.ADMIN, "test", True),
        (Role.ADMIN, "dev", True),
        # Positif: Kontributor membuka isi kasus test (AC3); kasus dev tidak (D9).
        (Role.CONTRIBUTOR, "test", True),
        (Role.CONTRIBUTOR, "dev", False),
        # Positif dan negatif: Developer AI hanya kasus dev, kasus test disamarkan (D9 aturan 2).
        (Role.DEVELOPER_AI, "dev", True),
        (Role.DEVELOPER_AI, "test", False),
        # Negatif: Viewer murni tidak boleh melihat isi kasus sama sekali (AC3).
        (Role.VIEWER, "test", False),
        (Role.VIEWER, "dev", False),
    ],
)
def test_penelusuran_mengikuti_matriks_d9(role, split_tag, boleh):
    """Positif dan negatif: siapa membuka isi kasus dari penelusuran laporan (AC3)."""
    # Act
    visible = can_view_drilldown_content(role, split_tag)

    # Assert
    assert visible is boleh


@pytest.mark.parametrize("role", [Role.ADMIN, Role.CONTRIBUTOR, Role.DEVELOPER_AI])
@pytest.mark.parametrize("split_tag", [None, "", "Test", "both"])
def test_penelusuran_tag_kosong_atau_tak_dikenal_tertutup_kecuali_admin(role, split_tag):
    """Edge: fail-closed seperti tabel sesi. Kasus tanpa tag diperlakukan seperti test (D9
    aturan 4), jadi hanya Admin yang membukanya."""
    # Act
    visible = can_view_drilldown_content(role, split_tag)

    # Assert
    assert visible is (role is Role.ADMIN)


def test_penelusuran_tidak_mengubah_aturan_sesi_untuk_kontributor():
    """Negatif: membuka Kontributor di penelusuran tidak membuka isi kasus di halaman sesi."""
    # Act
    di_sesi = can_view_case_content(Role.CONTRIBUTOR, "test")
    di_penelusuran = can_view_drilldown_content(Role.CONTRIBUTOR, "test")

    # Assert
    assert (di_sesi, di_penelusuran) == (False, True)
