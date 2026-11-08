"""QA pemulihan restart server lewat HTTP. SCRUM-77 SCRUM-221.

Pemulihan startup (`recover_after_restart`) dijalankan pada sesi yang ditinggal worker,
lalu halaman Eksekusi membaca hasilnya dan Admin menekan Lanjutkan. Hanya provider AI
yang diganti dengan provider terkontrol.

Label: Positif = alur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.modules.runs.models import Attempt, AttemptStatus, CallLog, SessionStatus, StopReason
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


def _panggilan_terputus(siap, sesi_id, *, upaya_sebelumnya: bool = False) -> Attempt:
    """Tandai satu percobaan sedang terbang saat server mati, lalu sesi kembali Berjalan.

    Klaim worker menulis status RUNNING dan started_at sebelum panggilan; baris call_logs
    upaya itu baru ditulis setelah respons kembali, jadi crash di tengah panggilan tidak
    meninggalkan baris untuk upaya tersebut. `upaya_sebelumnya` meniru percobaan yang
    sudah pernah dicoba (Coba ulang) sehingga sudah punya satu baris call_logs.
    """
    menggantung = siap.db.scalar(
        select(Attempt)
        .where(Attempt.session_id == sesi_id, Attempt.status == AttemptStatus.PENDING)
        .order_by(Attempt.id)
    )
    menggantung.status = AttemptStatus.RUNNING
    menggantung.started_at = datetime.now(UTC)
    if upaya_sebelumnya:
        siap.db.add(
            CallLog(
                attempt_id=menggantung.id,
                product_id=siap.produk.id,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                duration_ms=0,
                cost_idr=150,
                usd_idr_rate=16000,
                model_version="",
                result="technical_failure",
                failure_kind="provider_error",
                raw={},
                try_no=1,
            )
        )
    siap.db.commit()
    SessionStatusRepository(siap.db).change_status(
        sesi_id, SessionStatus.RUNNING, at=datetime.now(UTC)
    )
    return menggantung


def _restart_lalu_lanjutkan(siap):
    """Server mati saat panggilan kedua terbang, menyala lagi, Admin menekan Lanjutkan."""
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    menggantung = _panggilan_terputus(siap, sesi_id)
    panggilan_sebelum = len(provider.calls)
    recover_after_restart(siap.db)
    siap.db.expire_all()
    setelah_restart = baca_detail(siap, sesi_id)
    jalankan()
    tanpa_tombol = len(provider.calls) - panggilan_sebelum
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()
    siap.db.expire_all()
    return sesi_id, provider, jalankan, menggantung, setelah_restart, tanpa_tombol, lanjut


def test_restart_saat_panggilan_berjalan_tidak_mengirim_ulang_panggilan_berbayar(siap):
    """Edge: AC8 SCRUM-268, percobaan terputus jadi gagal teknis; Lanjutkan menyelesaikan sisa kasus."""
    # Arrange + Act
    sesi_id, provider, _, menggantung, setelah_restart, tanpa_tombol, lanjut = (
        _restart_lalu_lanjutkan(siap)
    )
    selesai = baca_detail(siap, sesi_id)

    # Assert
    assert setelah_restart["status"] == "paused"
    assert status_percobaan(setelah_restart).count("technical_failure") == 1
    assert "running" not in status_percobaan(setelah_restart)
    assert tanpa_tombol == 0
    assert lanjut.status_code == 202
    assert len(provider.calls) == TOTAL - 1  # yang terputus tidak pernah dikirim ulang
    assert selesai["status"] == "finished_with_failures"
    assert selesai["report_eligible"] is False
    assert siap.db.get(Attempt, menggantung.id).status == AttemptStatus.TECHNICAL_FAILURE
    assert not list(siap.db.scalars(select(CallLog).where(CallLog.attempt_id == menggantung.id)))


def test_restart_lalu_coba_ulang_percobaan_terputus_menyelesaikan_sesi(siap):
    """Positif: AC3 AC8, panggilan ulang hanya terjadi setelah Admin memilih Coba ulang."""
    # Arrange
    sesi_id, provider, jalankan, menggantung, *_ = _restart_lalu_lanjutkan(siap)

    # Act
    coba_ulang = siap.sebagai(Role.ADMIN).post(
        f"{URL}/{sesi_id}/retry", json={"attempt_ids": [str(menggantung.id)]}
    )
    jalankan()
    selesai = baca_detail(siap, sesi_id)

    # Assert
    assert coba_ulang.status_code == 202
    assert coba_ulang.json()["retried_attempt_ids"] == [str(menggantung.id)]
    assert len(provider.calls) == TOTAL
    assert selesai["status"] == "finished"
    assert selesai["report_eligible"] is True
    assert status_percobaan(selesai) == ["succeeded"] * TOTAL


def test_restart_lalu_terima_apa_adanya_membuat_sesi_layak_laporan(siap):
    """Positif: AC8 AC9, Admin menerima hasil tanpa membayar ulang panggilan yang terputus."""
    # Arrange
    sesi_id, provider, *_ = _restart_lalu_lanjutkan(siap)

    # Act
    terima = siap.sebagai(Role.ADMIN).patch(
        f"{URL}/{sesi_id}/accept", json={"reason": "Server mati saat satu panggilan berjalan"}
    )
    detail = baca_detail(siap, sesi_id)

    # Assert
    assert terima.status_code == 200
    assert detail["report_eligible"] is True
    assert len(provider.calls) == TOTAL - 1


def test_restart_dengan_baris_call_logs_upaya_lama_tetap_dihitung(siap):
    """Edge: percobaan yang pernah dicoba lalu terputus; baris lama tetap, coba ulang jadi upaya ke-2."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    menggantung = _panggilan_terputus(siap, sesi_id, upaya_sebelumnya=True)
    recover_after_restart(siap.db)
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()

    # Act
    coba_ulang = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/retry", json={"all_failed": True})
    jalankan()
    siap.db.expire_all()

    # Assert
    assert lanjut.status_code == 202
    assert coba_ulang.status_code == 202
    logs = list(
        siap.db.scalars(
            select(CallLog).where(CallLog.attempt_id == menggantung.id).order_by(CallLog.try_no)
        )
    )
    assert [(log.try_no, log.is_manual_retry) for log in logs] == [(1, False), (2, True)]
    assert logs[0].cost_idr == 150
    assert baca_detail(siap, sesi_id)["status"] == "finished"
    assert len(provider.calls) == TOTAL


def test_restart_lalu_batalkan_lalu_lanjutkan_tetap_bisa_selesai(siap):
    """Edge: AC8, dari Dibatalkan pun Lanjutkan tidak lagi ditolak ATTEMPT_RECOVERY_REQUIRED."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    _panggilan_terputus(siap, sesi_id)
    recover_after_restart(siap.db)
    admin = siap.sebagai(Role.ADMIN)

    # Act
    batal = admin.post(f"{URL}/{sesi_id}/cancel")
    lanjut = admin.post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()

    # Assert
    assert batal.status_code == 202
    assert lanjut.status_code == 202
    assert baca_detail(siap, sesi_id)["status"] == "finished_with_failures"
    assert len(provider.calls) == TOTAL - 1


def test_lanjutkan_sesi_macet_tanpa_restart_menandai_percobaan_yatim(siap):
    """Edge: sesi yang sudah Dihentikan dengan percobaan Berjalan sebelum perbaikan ini dideploy."""
    # Arrange
    sesi_id, provider, jalankan = berhenti_di_panggilan_pertama(siap, "pause")
    menggantung = _panggilan_terputus(siap, sesi_id)
    SessionStatusRepository(siap.db).change_status(
        sesi_id, SessionStatus.PAUSED, at=datetime.now(UTC), stop_reason=StopReason.RESTART
    )

    # Act
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    provider.on_call = None
    jalankan()
    siap.db.expire_all()

    # Assert
    assert lanjut.status_code == 202
    assert siap.db.get(Attempt, menggantung.id).status == AttemptStatus.TECHNICAL_FAILURE
    assert baca_detail(siap, sesi_id)["status"] == "finished_with_failures"
    assert len(provider.calls) == TOTAL - 1


def test_lanjutkan_yang_ditolak_tidak_mengubah_percobaan_yatim(siap, monkeypatch):
    """Negatif: Lanjutkan yang ditolak 409 tidak menulis apa pun, termasuk penandaan gagal."""
    # Arrange
    sesi_id, _, _ = berhenti_di_panggilan_pertama(siap, "pause")
    menggantung = _panggilan_terputus(siap, sesi_id)
    SessionStatusRepository(siap.db).change_status(
        sesi_id, SessionStatus.PAUSED, at=datetime.now(UTC), stop_reason=StopReason.RESTART
    )
    # SCRUM-267: a busy slot now queues the resume, so reject it through the remaining slot
    # conflict (every commit hits the one-running index) and check that the rollback keeps
    # the orphan attempt untouched.
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    def slot_taken():
        original = sqlite3.IntegrityError(
            "UNIQUE constraint failed: measurement_sessions.session_type"
        )
        original.sqlite_errorname = "SQLITE_CONSTRAINT_UNIQUE"
        raise IntegrityError("synthetic statement", {}, original)

    monkeypatch.setattr(siap.db, "commit", slot_taken)

    # Act
    lanjut = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/resume")
    siap.db.expire_all()

    # Assert
    assert lanjut.status_code == 409
    assert lanjut.json()["code"] == "ANOTHER_SESSION_RUNNING"
    assert siap.db.get(Attempt, menggantung.id).status == AttemptStatus.RUNNING
    assert baca_detail(siap, sesi_id)["status"] == "paused"
