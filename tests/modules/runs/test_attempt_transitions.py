"""Aturan perpindahan status percobaan (attempts) per D4 bagian B. PBI-12 AC2, AC3, SCRUM-214.

#97 "Part 2" butir 4 (status tingkat panggilan). Satu tabel seperti session_transitions:
- pending -> running: worker mengambil percobaan (#98, SCRUM-249).
- running -> succeeded | technical_failure: jawaban sah, atau gagal/timeout 240 detik.
- technical_failure -> pending: hanya lewat coba ulang manual (#99, SCRUM-218). D4
  menggambar panah langsung ke running; kode membuka ulang ke pending supaya klaim worker
  tetap satu-satunya jalan masuk ke running.
- succeeded: akhir. Tidak ada coba ulang otomatis (AC2).

Hanya percobaan otomatis yang diatur; pencatatan produk manual (PBI-22) belum dirancang.
Penjaga database untuk tabel yang sama ada di tests/integration/test_attempt_status_guard_pg.py.

Partisi masukan (E3): 4 x 4 pasangan status, satu kasus per pasangan.
Label: Positif = sah, Negatif = ditolak, Edge = batas perilaku.
"""

import importlib

import pytest

from app.modules.runs.models import AttemptStatus
from app.shared.exceptions import ConflictError

_A = AttemptStatus
SAH = {
    (_A.PENDING, _A.RUNNING),
    (_A.RUNNING, _A.SUCCEEDED),
    (_A.RUNNING, _A.TECHNICAL_FAILURE),
    (_A.RUNNING, _A.PENDING),
    (_A.TECHNICAL_FAILURE, _A.PENDING),
}

PASANGAN = [
    pytest.param(asal, tujuan, (asal, tujuan) in SAH, id=f"{asal}->{tujuan}")
    for asal in AttemptStatus
    for tujuan in AttemptStatus
]


@pytest.fixture
def aturan():
    # Diimpor lewat fixture: modul yang belum ada membuat tes merah, bukan galat koleksi.
    return importlib.import_module("app.modules.runs.attempt_transitions")


@pytest.mark.parametrize(("asal", "tujuan", "sah"), PASANGAN)
def test_tabel_sesuai_d4_untuk_setiap_pasangan(aturan, asal, tujuan, sah):
    """Positif/Negatif: tabel memuat perpindahan sah termasuk running -> pending (SCRUM-217)."""
    # Act + Assert
    assert (tujuan in aturan.ALLOWED_ATTEMPT_TRANSITIONS[asal]) is sah


def test_running_ke_pending_diizinkan_untuk_pemulihan_restart(aturan):
    """Positif: pemulihan restart (SCRUM-217) boleh mengembalikan attempt running ke pending."""
    # Act & Assert (tidak raise ConflictError)
    aturan.ensure_attempt_transition(AttemptStatus.RUNNING, AttemptStatus.PENDING)


@pytest.mark.parametrize(("asal", "tujuan", "sah"), PASANGAN)
def test_penjaga_menolak_pasangan_di_luar_tabel(aturan, asal, tujuan, sah):
    """Negatif: pasangan di luar tabel ditolak 409 INVALID_ATTEMPT_TRANSITION beserta detailnya."""
    if sah:
        # Act + Assert
        aturan.ensure_attempt_transition(asal, tujuan)
        return

    # Act
    with pytest.raises(ConflictError) as error:
        aturan.ensure_attempt_transition(asal, tujuan)

    # Assert
    assert error.value.code == "INVALID_ATTEMPT_TRANSITION"
    assert error.value.details == {"from": asal.value, "to": tujuan.value}


def test_setiap_status_punya_baris_di_tabel(aturan):
    """Edge: status baru di enum wajib diputuskan di tabel, tidak diam-diam tanpa aturan."""
    # Act + Assert
    assert set(aturan.ALLOWED_ATTEMPT_TRANSITIONS) == set(AttemptStatus)


def test_succeeded_adalah_akhir(aturan):
    """Negatif: tidak ada coba ulang atas jawaban sah (AC2, AC3 hanya yang gagal)."""
    # Act + Assert
    assert aturan.ALLOWED_ATTEMPT_TRANSITIONS[AttemptStatus.SUCCEEDED] == frozenset()


def test_status_dari_database_sebagai_teks_diterima(aturan):
    """Edge: kolom dibaca sebagai teks; aturannya sama."""
    # Act + Assert
    aturan.ensure_attempt_transition("pending", "running")
    with pytest.raises(ConflictError):
        aturan.ensure_attempt_transition("succeeded", "pending")


def test_status_tidak_dikenal_ditolak(aturan):
    """Negatif: status di luar enum adalah ValueError, bukan perpindahan yang ditolak."""
    # Act + Assert
    with pytest.raises(ValueError):
        aturan.ensure_attempt_transition("pending", "retrying")
