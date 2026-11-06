"""QA perjalanan kontrol sesi lewat HTTP: jeda, lanjutkan, batalkan. SCRUM-77 SCRUM-221.

Tes endpoint SCRUM-216 mengecek satu aksi dan baris database. Tes ini menekan tombol
lewat HTTP saat worker nyata sedang memanggil provider, lalu membaca hasilnya lewat
endpoint yang dipakai halaman Eksekusi: detail, progres, dan daftar. Hanya provider AI
yang diganti dengan provider terkontrol.

Label: Positif = alur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

import pytest
from sqlalchemy import func, select

from app.modules.notifications.models import Notification
from app.modules.runs.models import CallLog
from app.shared.security import Role
from tests.modules.runs.session_qa import (
    URL,
    baca_detail,
    baca_progres,
    berhenti_di_panggilan_pertama,
    jawaban_selesai,
    status_percobaan,
)

TOTAL = 9  # 3 kasus x 3 pengulangan
KUNCI_PROGRES = {
    "completed_calls",
    "total_calls",
    "percent_complete",
    "failed_calls",
    "percent_failed",
    "remaining_seconds",
    "running_cost_idr",
    "remaining_budget_idr",
    "started_at",
    "updated_at",
}


def test_jeda_menyimpan_panggilan_berjalan_lalu_lanjutkan_hanya_memproses_sisa(siap):
    """Positif: AC7 dan AC8, jeda tidak membuang hasil dan lanjutkan tidak mengulang panggilan."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    dijeda = baca_detail(siap, sesi_id)
    progres_dijeda = baca_progres(siap, sesi_id)
    jawaban_dijeda = jawaban_selesai(dijeda)

    # Act
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()
    selesai = baca_detail(siap, sesi_id)

    # Assert
    assert dijeda["status"] == "paused"
    assert dijeda["stop_reason"] == "user"
    assert dijeda["report_eligible"] is False
    assert status_percobaan(dijeda).count("succeeded") == 1
    assert status_percobaan(dijeda).count("pending") == TOTAL - 1
    assert progres_dijeda["completed_calls"] == 1
    assert lanjut.status_code == 202
    assert lanjut.json()["status"] == "running"
    assert selesai["status"] == "finished"
    assert status_percobaan(selesai) == ["succeeded"] * TOTAL
    assert len(jawaban_dijeda) == 1
    assert jawaban_selesai(selesai).items() >= jawaban_dijeda.items()
    assert len(provider.calls) == TOTAL
    assert siap.db.scalar(select(func.count()).select_from(CallLog)) == TOTAL
    assert selesai["report_eligible"] is True
    assert baca_progres(siap, sesi_id)["percent_complete"] == "100.0"


def test_batalkan_menyimpan_hasil_dan_tidak_layak_laporan_sampai_selesai_lagi(siap):
    """Positif: AC7, dibatalkan tidak jadi dasar laporan sampai dilanjutkan dan selesai."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "cancel")
    admin = siap.sebagai(Role.ADMIN)

    # Act
    dibatalkan = baca_detail(siap, sesi_id)
    panggilan_sebelum = len(provider.calls)
    jalankan()  # worker tidak boleh mengambil sesi yang dibatalkan
    sesudah_worker = baca_detail(siap, sesi_id)
    lanjut = admin.post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()
    selesai = baca_detail(siap, sesi_id)

    # Assert
    assert dibatalkan["status"] == "cancelled"
    assert dibatalkan["report_eligible"] is False
    assert status_percobaan(dibatalkan).count("succeeded") == 1
    assert len(provider.calls) - panggilan_sebelum == TOTAL - 1
    assert sesudah_worker["status"] == "cancelled"
    assert lanjut.status_code == 202
    assert selesai["status"] == "finished"
    assert selesai["report_eligible"] is True


def test_jeda_lalu_batalkan_tetap_menyimpan_hasil_tanpa_notifikasi_selesai(siap):
    """Edge: jeda lalu batalkan, hasil yang selesai utuh dan pemulai tidak diberi notifikasi selesai."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    jawaban_dijeda = jawaban_selesai(baca_detail(siap, sesi_id))

    # Act
    batal = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/cancel")
    jalankan()
    detail = baca_detail(siap, sesi_id)

    # Assert
    assert batal.status_code == 202
    assert batal.json()["status"] == "cancelled"
    assert detail["status"] == "cancelled"
    assert len(jawaban_dijeda) == 1
    assert jawaban_selesai(detail) == jawaban_dijeda
    assert len(provider.calls) == 1
    assert (
        siap.db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.link == f"/runs/{sesi_id}")
        )
        == 0
    )


@pytest.mark.parametrize("aksi", ["pause", "cancel", "resume"])
def test_sesi_selesai_tidak_bisa_dikontrol_lewat_api(siap, aksi):
    """Negatif: AC9, transisi tidak valid dari Selesai ditolak 409 dan status tidak berubah."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()
    assert baca_detail(siap, sesi_id)["status"] == "finished"

    # Act
    response = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/{aksi}")

    # Assert
    assert response.status_code == 409
    assert response.json()["code"] in {"INVALID_TRANSITION", "SESSION_NOT_RESUMABLE"}
    assert baca_detail(siap, sesi_id)["status"] == "finished"


def test_versi_model_berubah_setelah_lanjutkan_menghentikan_sesi_dan_menolak_lanjut_lagi(siap):
    """Negatif: AC8, versi model berbeda dari saat mulai, sesi berhenti dan harus mulai baru."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    asli = provider.ask

    def versi_baru(*args, **kwargs):
        respons = asli(*args, **kwargs)
        respons.model_version = "model-v2"
        return respons

    provider.ask = versi_baru
    provider.on_call = None
    panggilan_sebelum = len(provider.calls)

    # Act
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    jalankan()
    detail = baca_detail(siap, sesi_id)
    lagi = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")

    # Assert
    assert lanjut.status_code == 202
    assert len(provider.calls) - panggilan_sebelum == 1  # tidak ada panggilan berbayar lanjutan
    assert detail["status"] == "paused"
    assert detail["stop_reason"] == "error"
    assert detail["resume_error_code"] == "MODEL_VERSION_CHANGED"
    assert lagi.status_code == 409
    assert lagi.json()["code"] == "MODEL_VERSION_CHANGED"
    assert baca_detail(siap, sesi_id)["status"] == "paused"


def test_progres_tidak_memuat_coba_ulang_berjalan_dan_penanda_produk_bermasalah(siap):
    """Edge: AC5, layar progres hanya memuat angka yang disepakati, tidak ada penanda tambahan."""
    # Arrange
    sesi_id, _, _ = berhenti_di_panggilan_pertama(siap, "pause")

    # Act
    progres = baca_progres(siap, sesi_id)

    # Assert
    assert set(progres) == KUNCI_PROGRES
    assert progres["completed_calls"] == 1
    assert progres["total_calls"] == TOTAL
    assert progres["failed_calls"] == 0
