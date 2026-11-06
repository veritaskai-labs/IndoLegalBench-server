"""QA coba ulang dan terima apa adanya lewat HTTP. SCRUM-77 SCRUM-221.

Worker nyata menjalankan sesi dengan satu panggilan gagal, lalu halaman Eksekusi
membaca hasilnya dan Admin memutuskan: coba ulang atau terima apa adanya. Hanya
provider AI yang diganti dengan provider terkontrol.

Label: Positif = alur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.modules.runs.models import Attempt, CallLog
from app.shared.security import Role
from tests.modules.runs.session_qa import (
    URL,
    baca_detail,
    baca_progres,
    status_percobaan,
)
from tests.modules.runs.test_execution_runtime import Provider, runner, start

TOTAL = 9  # 3 kasus x 3 pengulangan


def _selesai_dengan_satu_gagal(siap):
    """Sesi selesai; panggilan pertama gagal dan tidak dicoba ulang otomatis."""
    sesi_id = start(siap)
    provider = Provider(behavior="mixed")
    assert runner(siap, provider)() is True
    siap.db.expire_all()
    return sesi_id, provider


def _id_percobaan_gagal(detail):
    return [
        a["id"]
        for kasus in detail["cases"]
        for a in kasus["attempts"]
        if a["status"] == "technical_failure"
    ]


def test_panggilan_gagal_tidak_dicoba_ulang_otomatis_dan_sesi_tidak_layak_laporan(siap):
    """Positif: AC2 dan AC4, satu kegagalan teknis dicatat, sisanya jalan, tanpa coba ulang otomatis."""
    # Arrange + Act
    sesi_id, provider = _selesai_dengan_satu_gagal(siap)
    detail = baca_detail(siap, sesi_id)
    progres = baca_progres(siap, sesi_id)

    # Assert
    assert detail["status"] == "finished_with_failures"
    assert detail["report_eligible"] is False
    assert len(provider.calls) == TOTAL
    assert sorted(status_percobaan(detail)) == ["succeeded"] * (TOTAL - 1) + ["technical_failure"]
    (gagal,) = [
        a for k in detail["cases"] for a in k["attempts"] if a["status"] == "technical_failure"
    ]
    assert gagal["answer_text"] is None
    assert progres["failed_calls"] == 1
    assert progres["completed_calls"] == TOTAL


def test_coba_ulang_menambah_upaya_tanpa_menambah_pengulangan_sampel(siap):
    """Positif: AC3, coba ulang membuka baris upaya baru dan pengulangan sampel tetap 1 sampai 3."""
    # Arrange
    sesi_id, _ = _selesai_dengan_satu_gagal(siap)
    gagal = _id_percobaan_gagal(baca_detail(siap, sesi_id))
    ulang = Provider()

    # Act
    response = siap.sebagai(Role.ADMIN).post(f"{URL}/{sesi_id}/retry", json={"all_failed": True})
    assert runner(siap, ulang)() is True
    detail = baca_detail(siap, sesi_id)

    # Assert
    assert response.status_code == 202
    assert response.json()["retried_attempt_ids"] == gagal
    assert len(ulang.calls) == 1
    assert detail["status"] == "finished"
    assert detail["report_eligible"] is True
    assert status_percobaan(detail) == ["succeeded"] * TOTAL
    assert all([a["repetition_no"] for a in k["attempts"]] == [1, 2, 3] for k in detail["cases"])
    assert siap.db.scalar(select(func.count()).select_from(Attempt)) == TOTAL
    percobaan_ke = list(
        siap.db.scalars(
            select(CallLog.try_no)
            .where(CallLog.attempt_id == uuid.UUID(gagal[0]))
            .order_by(CallLog.try_no)
        )
    )
    assert percobaan_ke == [1, 2]
    assert siap.db.scalar(select(func.count()).select_from(CallLog)) == TOTAL + 1


def test_terima_apa_adanya_menyimpan_alasan_pelaku_dan_waktu_lalu_menutup_coba_ulang(siap):
    """Positif: AC9, tersimpan dengan alasan, pelaku, waktu; sesi layak laporan dan tidak bisa dikontrol."""
    # Arrange
    sesi_id, _ = _selesai_dengan_satu_gagal(siap)
    admin = siap.sebagai(Role.ADMIN)

    # Act
    terima = admin.patch(
        f"{URL}/{sesi_id}/accept", json={"reason": "  Gangguan penyedia, hasil cukup  "}
    )
    detail = baca_detail(siap, sesi_id)
    coba_ulang = admin.post(f"{URL}/{sesi_id}/retry", json={"all_failed": True})

    # Assert
    assert terima.status_code == 200
    assert detail["status"] == "finished_with_failures"
    assert detail["accepted_as_is_reason"] == "Gangguan penyedia, hasil cukup"
    assert detail["accepted_as_is_by"] is not None
    assert detail["accepted_as_is_at"] is not None
    assert detail["report_eligible"] is True
    assert coba_ulang.status_code == 409
    assert coba_ulang.json()["code"] == "SESSION_ACCEPTED"


@pytest.mark.parametrize("body", [{}, {"reason": None}, {"reason": ""}, {"reason": "   \t "}])
def test_terima_apa_adanya_tanpa_alasan_ditolak_422(siap, body):
    """Negatif: AC9, alasan kosong, null, atau spasi saja ditolak dan sesi tidak tertandai."""
    # Arrange
    sesi_id, _ = _selesai_dengan_satu_gagal(siap)

    # Act
    response = siap.sebagai(Role.ADMIN).patch(f"{URL}/{sesi_id}/accept", json=body)

    # Assert
    assert response.status_code == 422
    assert response.json()["code"] == "REASON_REQUIRED"
    detail = baca_detail(siap, sesi_id)
    assert detail["accepted_as_is_at"] is None
    assert detail["report_eligible"] is False


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER, Role.DEVELOPER_AI])
def test_non_admin_tidak_bisa_menerima_atau_mencoba_ulang_sesi_dengan_kegagalan(siap, role):
    """Negatif: AC9, non-Admin mendapat 403 untuk terima apa adanya dan sesi tidak berubah."""
    # Arrange
    sesi_id, _ = _selesai_dengan_satu_gagal(siap)

    # Act
    terima = siap.sebagai(role).patch(f"{URL}/{sesi_id}/accept", json={"reason": "alasan"})

    # Assert
    assert terima.status_code == 403
    assert baca_detail(siap, sesi_id)["accepted_as_is_at"] is None
