"""Persen kasus selesai untuk layar progres dan daftar sesi. SCRUM-77 SCRUM-265.

Aturan tiket (PBI-12 AC5, feedback UAT):
- Persen dibulatkan ke bilangan bulat dan dibatasi 0 sampai 100.
- Total kasus nol tidak menyebabkan pembagian dengan nol.
- Pembulatan ke bawah (floor): 100 hanya muncul bila semua kasus memang selesai.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: masukan yang tidak masuk akal.
- Edge: batas atau kasus pinggir.
"""

import pytest

from app.modules.runs.case_progress import CaseProgress, case_progress


def test_tujuh_dari_dua_puluh_kasus_berarti_35_persen():
    """Positif: contoh tiket, 7 dari 20 kasus."""
    # Act
    hasil = case_progress(completed_cases=7, total_cases=20)

    # Assert
    assert hasil == CaseProgress(completed_cases=7, total_cases=20, percent_cases_complete=35)


def test_semua_kasus_selesai_berarti_100_persen():
    """Positif: sesi selesai."""
    # Act
    hasil = case_progress(completed_cases=20, total_cases=20)

    # Assert
    assert hasil.percent_cases_complete == 100


def test_sesi_tanpa_kasus_bernilai_nol_tanpa_pembagian_nol():
    """Edge: sesi masih antre, attempts belum dibuat."""
    # Act
    hasil = case_progress(completed_cases=0, total_cases=0)

    # Assert
    assert hasil == CaseProgress(completed_cases=0, total_cases=0, percent_cases_complete=0)


def test_belum_ada_kasus_selesai_bernilai_nol():
    """Edge: sesi baru mulai."""
    # Act
    hasil = case_progress(completed_cases=0, total_cases=20)

    # Assert
    assert hasil.percent_cases_complete == 0


@pytest.mark.parametrize(
    ("selesai", "total", "persen"),
    [(199, 200, 99), (1, 3, 33), (2, 3, 66), (1, 200, 0)],
)
def test_persen_dibulatkan_ke_bawah(selesai, total, persen):
    """Edge: 199/200 = 99,5 tetap 99 supaya 100 tidak muncul sebelum semua selesai."""
    # Act
    hasil = case_progress(completed_cases=selesai, total_cases=total)

    # Assert
    assert hasil.percent_cases_complete == persen


def test_persen_dibatasi_100_bila_selesai_melebihi_total():
    """Negatif: angka agregasi rusak tidak boleh membuat bar progres lebih dari penuh."""
    # Act
    hasil = case_progress(completed_cases=25, total_cases=20)

    # Assert
    assert hasil.percent_cases_complete == 100


def test_persen_dibatasi_0_bila_selesai_negatif():
    """Negatif: angka negatif tidak boleh membuat bar progres mundur di bawah nol."""
    # Act
    hasil = case_progress(completed_cases=-1, total_cases=20)

    # Assert
    assert hasil.percent_cases_complete == 0
