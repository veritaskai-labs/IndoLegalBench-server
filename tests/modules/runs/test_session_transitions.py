"""Perpindahan status sesi pengukuran per D4. PBI-12 AC7, AC9, SCRUM-214.

Semua perpindahan status sesi lewat satu aturan, supaya tidak ada transisi liar.
Matriks di bawah menguji setiap pasangan (status sekarang, status tujuan): 12 x 12 = 144
pasangan, termasuk ke status yang sama. Yang tidak ada di D4 atau D8 ditolak dengan 409
INVALID_TRANSITION, termasuk perpindahan antara status otomatis dan status manual.

Panah D4 (Bagian A) yang diizinkan:
- queued -> running (giliran antrean), queued -> cancelled ([USULAN SA] boleh).
- queued -> failed: sesi gagal disiapkan sebelum mulai (produk dihapus, tidak ada
  kasus). Tidak digambar di D4, tetapi sesuai [USULAN SA] "failed = kesalahan fatal
  sejak awal" dan sudah dilakukan SessionQueue (SCRUM-207).
- running -> finished, finished_with_failures, failed, paused, cancelled.
- finished_with_failures -> running (coba ulang yang gagal, PBI-12 AC3).
- paused -> running dan cancelled -> running (Lanjutkan, PBI-12 AC8). Penjaga versi
  model dan batas hari ada di SCRUM-216, bukan di matriks ini.
- SCRUM-216: paused -> cancelled; cancelled -> queued untuk sesi yang belum
  pernah disiapkan. Keduanya disetujui pengguna pada 5 November 2026.

Nama status mengikuti enum di staging (finished, finished_with_failures), bukan label
D4 (completed, completed_with_failures).

Panah D8 (sesi manual, PBI-22 SCRUM-230) yang diizinkan:
- not_filled -> partially_filled, atau langsung complete bila jawaban pertama sudah
  melengkapi sesi (mis. 1 kasus x 1 percobaan).
- partially_filled -> complete (semua kasus x semua percobaan terisi).
- complete -> graded (Nilai, SCRUM-226) dan complete -> finalized (Tandai selesai).
- graded -> finalized. Selesai dari Lengkap atau Dinilai: keputusan SA 7 November 2026,
  karena urutan Selesai dan Nilai masih TBA. finalized tidak bisa dibuka kembali (AC8).

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import importlib
import itertools

import pytest

from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError

# String, bukan anggota enum, supaya file ini tetap terkumpul walau enum belum
# punya paused dan cancelled (tes merah per kasus, bukan galat koleksi).
_STATUS = [
    "queued",
    "running",
    "paused",
    "cancelled",
    "finished",
    "finished_with_failures",
    "failed",
    "not_filled",
    "partially_filled",
    "complete",
    "graded",
    "finalized",
]

_D4 = {
    ("queued", "running"),
    ("queued", "cancelled"),
    ("queued", "failed"),
    ("running", "finished"),
    ("running", "finished_with_failures"),
    ("running", "failed"),
    ("running", "paused"),
    ("running", "cancelled"),
    ("finished_with_failures", "running"),
    ("paused", "running"),
    ("cancelled", "running"),
    ("paused", "cancelled"),
    ("cancelled", "queued"),
}

_D8 = {
    ("not_filled", "partially_filled"),
    ("not_filled", "complete"),
    ("partially_filled", "complete"),
    ("complete", "graded"),
    ("complete", "finalized"),
    ("graded", "finalized"),
}

_SEMUA_PASANGAN = list(itertools.product(_STATUS, repeat=2))
_DITOLAK = [pasangan for pasangan in _SEMUA_PASANGAN if pasangan not in _D4 | _D8]


@pytest.fixture
def transisi():
    return importlib.import_module("app.modules.runs.session_transitions")


def test_enum_punya_tujuh_status_d4_dan_lima_status_d8():
    """Positif: lima status sesi manual D8 ada di enum yang sama dengan status D4 (D9)."""
    # Assert
    assert {status.value for status in SessionStatus} == set(_STATUS)


@pytest.mark.parametrize(("sekarang", "tujuan"), sorted(_D4 | _D8))
def test_transisi_d4_diizinkan(transisi, sekarang, tujuan):
    """Positif: setiap panah di D4 dan D8 boleh dilewati."""
    # Act / Assert
    transisi.ensure_transition(SessionStatus(sekarang), SessionStatus(tujuan))


@pytest.mark.parametrize(("sekarang", "tujuan"), _DITOLAK)
def test_transisi_di_luar_d4_ditolak(transisi, sekarang, tujuan):
    """Negatif: pasangan yang tidak digambar di D4 atau D8 ditolak dengan 409 INVALID_TRANSITION."""
    # Act
    with pytest.raises(ConflictError) as error:
        transisi.ensure_transition(SessionStatus(sekarang), SessionStatus(tujuan))

    # Assert
    assert error.value.code == "INVALID_TRANSITION"
    assert error.value.status_code == 409


@pytest.mark.parametrize("status", _STATUS)
def test_status_ke_dirinya_sendiri_ditolak(transisi, status):
    """Edge: tidak ada transisi diam-diam ke status yang sama (mis. running -> running)."""
    # Act / Assert
    with pytest.raises(ConflictError):
        transisi.ensure_transition(SessionStatus(status), SessionStatus(status))


@pytest.mark.parametrize("status", ["finished", "failed"])
def test_status_akhir_tidak_bisa_ke_mana_pun(transisi, status):
    """Edge: finished dan failed adalah status akhir, tidak ada panah keluar di D4."""
    # Assert
    assert transisi.ALLOWED_TRANSITIONS[SessionStatus(status)] == frozenset()


def test_tabel_mencakup_semua_status(transisi):
    """Edge: status baru di enum wajib diputuskan di tabel, tidak terlewat diam-diam."""
    # Assert
    assert set(transisi.ALLOWED_TRANSITIONS) == set(SessionStatus)


def test_pesan_galat_menyebut_kedua_status(transisi):
    """Negatif: pesan 409 menyebut status asal dan tujuan supaya mudah dilacak."""
    # Act
    with pytest.raises(ConflictError) as error:
        transisi.ensure_transition(SessionStatus.FINISHED, SessionStatus.RUNNING)

    # Assert
    assert "finished" in error.value.message
    assert "running" in error.value.message


def test_string_dari_database_diterima(transisi):
    """Edge: status dibaca dari database sebagai string; aturan yang sama berlaku."""
    # Act / Assert
    transisi.ensure_transition("queued", "running")
    with pytest.raises(ConflictError):
        transisi.ensure_transition("finished", "running")
