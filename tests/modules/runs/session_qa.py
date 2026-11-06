"""Bantuan bersama tes QA kontrol sesi dan pemulihan. SCRUM-77 SCRUM-221.

Semua tes QA SCRUM-221 menyiapkan sesi, menjalankan worker, dan membaca hasil dengan
cara yang sama. Ditulis sekali di sini, dan fixture serta provider dipakai ulang dari
tes runtime supaya tidak ada cara kedua menyiapkan sesi.
"""

import uuid

from app.shared.security import Role
from tests.modules.runs.test_execution_runtime import Provider, runner, start

URL = "/measurements"


def berhenti_di_panggilan_pertama(siap, aksi):
    """Mulai sesi, lalu tekan `aksi` (pause atau cancel) lewat HTTP saat panggilan pertama berjalan.

    Mengembalikan id sesi, providernya, dan fungsi untuk menjalankan worker lagi.
    """
    sesi_id = start(siap)
    provider = Provider()
    provider.on_call = lambda: siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/{aksi}")
    jalankan = runner(siap, provider)
    assert jalankan() is True
    siap.db.expire_all()
    return sesi_id, provider, jalankan


def baca_detail(siap, sesi_id: uuid.UUID, role: Role = Role.ADMIN) -> dict:
    """Detail sesi seperti yang dibaca halaman Eksekusi."""
    response = siap.sebagai(role).get(f"{URL}/{sesi_id}")
    assert response.status_code == 200
    return response.json()


def baca_progres(siap, sesi_id: uuid.UUID, role: Role = Role.ADMIN) -> dict:
    """Angka layar progres yang di-poll tiap 5 detik."""
    response = siap.sebagai(role).get(f"{URL}/{sesi_id}/progress")
    assert response.status_code == 200
    return response.json()


def jawaban_selesai(detail: dict) -> dict[str, str]:
    """Jawaban mentah percobaan yang berhasil, per id percobaan."""
    return {
        a["id"]: a["answer_text"]
        for kasus in detail["cases"]
        for a in kasus["attempts"]
        if a["status"] == "succeeded"
    }


def status_percobaan(detail: dict) -> list[str]:
    """Status semua percobaan, urut kasus lalu nomor pengulangan."""
    return [a["status"] for kasus in detail["cases"] for a in kasus["attempts"]]
