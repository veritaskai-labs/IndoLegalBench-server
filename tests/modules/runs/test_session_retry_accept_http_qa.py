"""QA coba ulang dan terima apa adanya lewat HTTP. SCRUM-77 SCRUM-221.

Worker nyata menjalankan sesi dengan satu panggilan gagal, lalu halaman Eksekusi
membaca hasilnya dan Admin memutuskan: coba ulang atau terima apa adanya. Hanya
provider AI yang diganti dengan provider terkontrol.

Label: Positif = alur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.modules.providers.adapters.ask_call import transport_failure
from app.modules.providers.adapters.base import FailureKind
from app.modules.providers.adapters.params import ASK_TIMEOUT_SECONDS
from app.modules.runs.models import Attempt, CallLog
from app.shared.security import Role
from tests.modules.cases.test_cases import ADMIN_LAIN
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
    assert detail["accepted_as_is_by"] == str(ADMIN_LAIN)
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
def test_non_admin_tidak_bisa_menerima_apa_adanya_sesi_dengan_kegagalan(siap, role):
    """Negatif: AC9, hanya Admin yang boleh menerima apa adanya; selain itu 403 FORBIDDEN."""
    # Arrange
    sesi_id, _ = _selesai_dengan_satu_gagal(siap)

    # Act
    terima = siap.sebagai(role).patch(f"{URL}/{sesi_id}/accept", json={"reason": "alasan"})

    # Assert
    assert terima.status_code == 403
    assert terima.json()["code"] == "FORBIDDEN"
    assert baca_detail(siap, sesi_id)["accepted_as_is_at"] is None


def test_timeout_panggilan_dicatat_gagal_teknis_no_response_dan_tidak_dicoba_ulang(siap):
    """Edge: AC2, worker memberi batas 240 detik; timeout jadi no_response tanpa coba ulang."""
    # Arrange
    sesi_id = start(siap)
    provider = Provider()
    asli = provider.ask

    def timeout_di_panggilan_pertama(prompt, **kwargs):
        if provider.calls:
            return asli(prompt, **kwargs)
        provider.calls.append((prompt, kwargs))
        # yang dikembalikan adapter nyata saat httpx timeout (adapters/http.py)
        return transport_failure(None, "connection timed out", 0, "")

    provider.ask = timeout_di_panggilan_pertama

    # Act
    assert runner(siap, provider)() is True
    detail = baca_detail(siap, sesi_id)

    # Assert
    assert ASK_TIMEOUT_SECONDS == 240.0
    assert {kwargs["timeout"] for _, kwargs in provider.calls} == {ASK_TIMEOUT_SECONDS}
    assert len(provider.calls) == TOTAL
    assert detail["status"] == "finished_with_failures"
    assert sorted(status_percobaan(detail)) == ["succeeded"] * (TOTAL - 1) + ["technical_failure"]
    assert detail["report_eligible"] is False
    assert siap.db.scalars(
        select(CallLog.failure_kind).where(CallLog.failure_kind.is_not(None))
    ).all() == [str(FailureKind.NO_RESPONSE)]
