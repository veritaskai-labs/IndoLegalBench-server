"""Kode alasan grading run gagal. SCRUM-78 SCRUM-289 (K-19).

Run yang gagal menyimpan satu kode terstruktur supaya Admin tahu penyebabnya. Kode berasal dari
galat judge tiap percobaan; bila beberapa penyebab bercampur, penyebab yang harus diperbaiki
Admin lebih dulu (konfigurasi, lalu anggaran) menang.

Label: Positif = tiap kode dikenali, Negatif = galat tak dikenal, Edge = campuran dan kosong.
"""

import pytest

from app.modules.runs.grading_failure import (
    GRADING_ERROR,
    JUDGE_PRICING_MISSING,
    NO_SCORE,
    resolve_failure_code,
)
from app.modules.runs.session_grade import (
    JUDGE_BUDGET_EXHAUSTED,
    JUDGE_INCOMPLETE,
    JUDGE_NOT_CONFIGURED,
    JUDGE_UNAVAILABLE,
)


@pytest.mark.parametrize(
    "kode",
    [JUDGE_NOT_CONFIGURED, JUDGE_UNAVAILABLE, JUDGE_BUDGET_EXHAUSTED, JUDGE_INCOMPLETE],
)
def test_tiap_kode_judge_yang_dikenal_dikembalikan_apa_adanya(kode):
    """Positif: semua percobaan gagal dengan satu kode, run memakai kode itu."""
    # Arrange
    galat = [kode, kode, kode]

    # Act
    hasil = resolve_failure_code(galat)

    # Assert
    assert hasil == kode


def test_harga_judge_tidak_lengkap_punya_kode_sendiri():
    """Positif: harga kosong dibedakan dari judge yang gagal dibangun."""
    # Arrange
    galat = [JUDGE_UNAVAILABLE, JUDGE_UNAVAILABLE]

    # Act
    hasil = resolve_failure_code(galat, pricing_missing=True)

    # Assert
    assert hasil == JUDGE_PRICING_MISSING == "judge_pricing_missing"


def test_galat_tak_dikenal_menjadi_grading_error():
    """Negatif: teks galat apa pun di luar daftar tidak ikut disimpan, hanya GRADING_ERROR."""
    # Arrange
    galat = ["Bearer sk-rahasia gagal di https://api.penyedia.test"]

    # Act
    hasil = resolve_failure_code(galat)

    # Assert
    assert hasil == GRADING_ERROR == "GRADING_ERROR"


@pytest.mark.parametrize(
    ("galat", "diharapkan"),
    [
        ([JUDGE_INCOMPLETE, JUDGE_BUDGET_EXHAUSTED], JUDGE_BUDGET_EXHAUSTED),
        ([JUDGE_BUDGET_EXHAUSTED, JUDGE_UNAVAILABLE], JUDGE_UNAVAILABLE),
        ([JUDGE_UNAVAILABLE, JUDGE_NOT_CONFIGURED], JUDGE_NOT_CONFIGURED),
        ([JUDGE_INCOMPLETE, "galat lain"], JUDGE_INCOMPLETE),
    ],
)
def test_beberapa_penyebab_bercampur_memakai_yang_paling_dulu_diperbaiki(galat, diharapkan):
    """Edge: urutan prioritas tetap, tidak bergantung urutan percobaan."""
    # Act
    hasil = resolve_failure_code(galat)
    terbalik = resolve_failure_code(list(reversed(galat)))

    # Assert
    assert hasil == diharapkan
    assert terbalik == diharapkan


def test_harga_tidak_lengkap_mengalahkan_galat_percobaan():
    """Edge: harga kosong membuat semua percobaan gagal, jadi itu penyebab sebenarnya."""
    # Arrange
    galat = [JUDGE_BUDGET_EXHAUSTED, JUDGE_INCOMPLETE]

    # Act
    hasil = resolve_failure_code(galat, pricing_missing=True)

    # Assert
    assert hasil == JUDGE_PRICING_MISSING


@pytest.mark.parametrize("galat", [[], [None, None]])
def test_tanpa_galat_judge_run_gagal_karena_tidak_ada_skor(galat):
    """Edge: jawaban kosong atau ditolak semua, tidak ada galat judge sama sekali."""
    # Act
    hasil = resolve_failure_code(galat)

    # Assert
    assert hasil == NO_SCORE == "NO_SCORE"


def test_galat_kosong_di_antara_galat_nyata_diabaikan():
    """Edge: percobaan yang berhasil (None) tidak mengubah kode run."""
    # Arrange
    galat = [None, JUDGE_UNAVAILABLE, None]

    # Act
    hasil = resolve_failure_code(galat)

    # Assert
    assert hasil == JUDGE_UNAVAILABLE


def test_resolver_menerima_generator():
    """Edge: bukan hanya list; worker menyuruhnya membaca percobaan dari beberapa kasus."""
    # Arrange
    galat = (kode for kode in [None, JUDGE_INCOMPLETE])

    # Act
    hasil = resolve_failure_code(galat)

    # Assert
    assert hasil == JUDGE_INCOMPLETE
