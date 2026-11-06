"""QA pemulihan restart server lewat HTTP. SCRUM-77 SCRUM-221.

Pemulihan startup (`recover_after_restart`) dijalankan pada sesi yang ditinggal worker,
lalu halaman Eksekusi membaca hasilnya dan Admin menekan Lanjutkan. Hanya provider AI
yang diganti dengan provider terkontrol.

Label: Positif = alur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.modules.runs.models import Attempt, AttemptStatus, SessionStatus
from app.modules.runs.recovery import recover_after_restart
from app.modules.runs.repository import SessionStatusRepository
from app.shared.security import Role
from tests.modules.runs.session_qa import (
    URL,
    baca_detail,
    berhenti_di_panggilan_pertama,
    status_percobaan,
)

TOTAL = 9  # 3 kasus x 3 pengulangan


def test_restart_saat_berjalan_menghentikan_sesi_dan_tidak_lanjut_sendiri(siap):
    """Edge: AC8, sesi Berjalan yang ditinggal worker jadi Dihentikan, lanjut hanya lewat tombol Lanjutkan."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    SessionStatusRepository(siap.db).change_status(
        sesi_id, SessionStatus.RUNNING, at=datetime.now(UTC)
    )  # worker hidup lalu server mati saat Berjalan
    panggilan_sebelum = len(provider.calls)

    # Act
    dipulihkan = recover_after_restart(siap.db)
    siap.db.expire_all()
    setelah_restart = baca_detail(siap, sesi_id)
    jalankan()  # worker baru menyala: tidak boleh mengambil sesi yang dihentikan
    tanpa_tombol = len(provider.calls) - panggilan_sebelum
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()
    selesai = baca_detail(siap, sesi_id)

    # Assert
    assert dipulihkan == [sesi_id]
    assert setelah_restart["status"] == "paused"
    assert setelah_restart["stop_reason"] == "restart"
    assert tanpa_tombol == 0
    assert lanjut.status_code == 202
    assert selesai["status"] == "finished"
    assert len(provider.calls) == TOTAL
    assert status_percobaan(selesai) == ["succeeded"] * TOTAL


def test_restart_saat_panggilan_berjalan_tidak_mengirim_ulang_panggilan_berbayar(siap):
    """Edge: AC8, percobaan yang tidak jelas nasibnya tidak dikirim ulang; lanjutkan ditolak 409.

    Percobaan menggantung sengaja tanpa baris call_logs agar hanya status RUNNING yang
    diuji. Crash nyata di tengah panggilan biasanya meninggalkan satu baris call_logs;
    tiket tindak lanjut pemulihan perlu menangani kedua bentuk itu.
    """
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    menggantung = siap.db.scalar(
        select(Attempt)
        .where(Attempt.session_id == sesi_id, Attempt.status == AttemptStatus.PENDING)
        .order_by(Attempt.id)
    )
    menggantung.status = AttemptStatus.RUNNING  # panggilan sedang terbang saat server mati
    siap.db.commit()
    SessionStatusRepository(siap.db).change_status(
        sesi_id, SessionStatus.RUNNING, at=datetime.now(UTC)
    )
    panggilan_sebelum = len(provider.calls)

    # Act
    recover_after_restart(siap.db)
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    jalankan()

    # Assert
    assert lanjut.status_code == 409
    assert lanjut.json()["code"] == "ATTEMPT_RECOVERY_REQUIRED"
    assert len(provider.calls) == panggilan_sebelum
    assert baca_detail(siap, sesi_id)["status"] == "paused"
    assert siap.db.get(Attempt, menggantung.id).status == AttemptStatus.RUNNING
