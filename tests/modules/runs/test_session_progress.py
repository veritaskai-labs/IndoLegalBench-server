"""Progres "X dari N" di daftar sesi pengukuran. PBI-11 AC10, SCRUM-210.

Aturan dari D2 (tabel attempts):
- N adalah jumlah baris attempts sesi, yaitu item snapshot x pengulangan.
- X adalah percobaan yang sudah punya hasil akhir: succeeded atau technical_failure.
- Coba ulang manual menambah baris call_logs, bukan attempts, jadi N tidak berubah.

Status diberikan sebagai jumlah per status, sama seperti hasil GROUP BY status.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: masukan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import pytest

from app.modules.runs.session_rules import SessionProgress, progress


def test_succeeded_dan_technical_failure_terhitung_selesai():
    """Positif: 2 berhasil + 1 gagal teknis dari 6 percobaan berarti 3 dari 6."""
    # Act
    hasil = progress({"succeeded": 2, "technical_failure": 1, "pending": 3})

    # Assert
    assert hasil == SessionProgress(completed=3, total=6)


def test_percobaan_yang_sedang_berjalan_belum_selesai():
    """Edge: running ikut N tetapi belum ikut X."""
    # Act
    hasil = progress({"running": 1, "succeeded": 1})

    # Assert
    assert hasil == SessionProgress(completed=1, total=2)


def test_sesi_tanpa_percobaan_bernilai_nol_dari_nol():
    """Edge: sesi masih antre, snapshot dan attempts baru dibuat saat sesi dijalankan."""
    # Act
    hasil = progress({})

    # Assert
    assert hasil == SessionProgress(completed=0, total=0)


def test_semua_gagal_teknis_tetap_selesai_penuh():
    """Edge: gagal teknis adalah hasil akhir. Coba ulang tidak menambah N."""
    # Act
    hasil = progress({"technical_failure": 4})

    # Assert
    assert hasil == SessionProgress(completed=4, total=4)


def test_status_yang_tidak_dikenal_ditolak():
    """Negatif: status baru (misalnya dari PBI-12) harus diputuskan dulu, bukan diam-diam dihitung."""
    # Act / Assert
    with pytest.raises(ValueError, match="cancelled"):
        progress({"succeeded": 1, "cancelled": 1})
