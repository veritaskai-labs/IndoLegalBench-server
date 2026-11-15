"""Kode gagal dan jumlah percobaan di endpoint progres dan riwayat. SCRUM-78 SCRUM-289 (K-19).

Admin melihat kenapa penilaian gagal dan berapa percobaan yang sudah dan belum dinilai, baik di
progres run terbaru (GET /grading-progress) maupun di tiap baris riwayat (GET /grading-runs).
"Coba Nilai Ulang" memakai alur yang sama dengan penilaian pertama: run baru ditambahkan, run
gagal yang lama tidak berubah, dan perkiraan biaya dibaca dari endpoint estimasi yang sama.

Isolasi: judge tiruan lewat build_judge (monkeypatch), kurs dan penghitung token tiruan.

Label: Positif = run bagus atau coba ulang, Negatif = run gagal, Edge = run lama tanpa progres.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.grading.models import GradingRun, GradingRunKind, GradingRunStatus
from app.modules.runs import grading_worker
from app.modules.runs.dependencies import get_exchange_rate, get_token_counter
from app.modules.runs.exchange_rate import ConfiguredExchangeRate
from app.shared.security import Role
from tests.modules.runs.test_grading_runs_per_judge import (  # noqa: F401  (fixture)
    pasang_penilai,
    pembuat,
    penilai,
    produk,
)
from tests.modules.runs.test_grading_worker import KURS, T0, _antre, _segarkan, sesi  # noqa: F401


def _nilai(as_role, db_session, sesi_id, **body) -> uuid.UUID:
    run_id = _antre(as_role, sesi_id, **body)
    grading_worker.claim_next_run(db_session, now=T0)
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)
    return run_id


def _gagal_dibangun(monkeypatch) -> None:
    def gagal(db, judge_id):
        raise RuntimeError("tidak bisa dibangun")

    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", gagal)


def _riwayat(as_role, sesi_id) -> list[dict]:
    res = as_role(Role.ADMIN).get(f"/measurements/{sesi_id}/grading-runs")
    assert res.status_code == 200, res.text
    return res.json()


def _progres(as_role, sesi_id) -> dict:
    res = as_role(Role.ADMIN).get(f"/measurements/{sesi_id}/grading-progress")
    assert res.status_code == 200, res.text
    return res.json()


def test_riwayat_run_gagal_membawa_kode_dan_jumlah_percobaan(
    as_role,
    db_session,
    sesi,  # noqa: F811
    monkeypatch,
):
    """Negatif: baris riwayat run gagal berisi kode gagal, dan tidak ada percobaan yang dinilai."""
    # Arrange
    _gagal_dibangun(monkeypatch)
    run_id = _nilai(as_role, db_session, sesi.id)

    # Act
    baris = _riwayat(as_role, sesi.id)

    # Assert
    assert len(baris) == 1
    assert baris[0]["grading_run_id"] == str(run_id)
    assert baris[0]["status"] == "failed"
    assert baris[0]["error_code"] == "judge_unavailable"
    assert baris[0]["attempts_total"] == 2
    assert baris[0]["attempts_graded"] == 0
    assert baris[0]["attempts_ungraded"] == 2


def test_run_gagal_tanpa_skor_melaporkan_nol_percobaan_dinilai(
    as_role,
    db_session,
    sesi,  # noqa: F811
    monkeypatch,
):
    """Negatif: run yang gagal total tidak boleh terlihat 2 dari 2 dinilai."""
    # Arrange
    _gagal_dibangun(monkeypatch)
    run_id = _nilai(as_role, db_session, sesi.id)

    # Act
    _, progres = _segarkan(db_session, run_id)
    status = _progres(as_role, sesi.id)

    # Assert
    assert progres.attempts_graded == 0
    assert status["status"] == "failed"
    assert status["error_code"] == "judge_unavailable"
    assert status["attempts_graded"] == 0
    assert status["attempts_ungraded"] == 2
    assert status["percent"] == 0


def test_riwayat_run_bagus_tanpa_kode_dan_semua_percobaan_dinilai(as_role, db_session, sesi):  # noqa: F811
    """Positif: run selesai membawa error_code kosong dan tidak ada percobaan tersisa."""
    # Arrange
    _nilai(as_role, db_session, sesi.id)

    # Act
    baris = _riwayat(as_role, sesi.id)

    # Assert
    assert baris[0]["status"] == "completed"
    assert baris[0]["error_code"] is None
    assert baris[0]["attempts_graded"] == baris[0]["attempts_total"] == 2
    assert baris[0]["attempts_ungraded"] == 0


def test_progres_run_antre_menghitung_semua_percobaan_belum_dinilai(as_role, sesi):  # noqa: F811
    """Positif: sebelum worker mengambilnya, belum ada percobaan yang dinilai."""
    # Arrange
    _antre(as_role, sesi.id)

    # Act
    status = _progres(as_role, sesi.id)

    # Assert
    assert status["status"] == "queued"
    assert status["error_code"] is None
    assert status["attempts_graded"] == 0
    assert status["attempts_ungraded"] == status["attempts_total"] == 2


def test_run_lama_dari_post_grade_tanpa_baris_progres_tetap_terbaca(as_role, sesi):  # noqa: F811
    """Edge: run dari POST /grade tidak punya grading_run_progress; jumlahnya dari hasil tersimpan."""
    # Arrange
    admin = as_role(Role.ADMIN)
    assert admin.post(f"/measurements/{sesi.id}/grade", json={}).status_code == 200

    # Act
    baris = _riwayat(as_role, sesi.id)

    # Assert
    assert len(baris) == 1
    assert baris[0]["error_code"] is None
    assert baris[0]["attempts_graded"] == baris[0]["attempts_total"] == 2
    assert baris[0]["attempts_ungraded"] == 0


def test_riwayat_sesi_tanpa_run_tetap_kosong(as_role, sesi):  # noqa: F811
    """Edge: sesi yang belum pernah dinilai."""
    # Act
    baris = _riwayat(as_role, sesi.id)

    # Assert
    assert baris == []


def test_riwayat_sesi_tak_dikenal_dijawab_404(as_role):
    """Negatif: sesi tidak ada."""
    # Act
    res = as_role(Role.ADMIN).get(f"/measurements/{uuid.uuid4()}/grading-runs")

    # Assert
    assert res.status_code == 404
    assert res.json()["code"] == "MEASUREMENT_NOT_FOUND"


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.CONTRIBUTOR])
def test_riwayat_dan_progres_hanya_untuk_admin(as_role, sesi, peran):  # noqa: F811
    """Negatif: peran selain Admin tidak boleh melihat alasan gagal."""
    # Act
    riwayat = as_role(peran).get(f"/measurements/{sesi.id}/grading-runs")
    progres = as_role(peran).get(f"/measurements/{sesi.id}/grading-progress")

    # Assert
    assert riwayat.status_code == 403
    assert progres.status_code == 403


class _Counter:
    def count(self, text: str, provider_type: str) -> int:
        return 10


def test_coba_nilai_ulang_menambah_run_baru_dan_run_gagal_tidak_berubah(
    as_role,
    db_session,
    sesi,  # noqa: F811
    pasang_penilai,  # noqa: F811
    monkeypatch,
):
    """Positif: setelah gagal, penilaian ulang jadi run baru; run gagal lama tetap utuh."""
    # Arrange
    _gagal_dibangun(monkeypatch)
    gagal_id = _nilai(as_role, db_session, sesi.id)
    pasang_penilai(sesi)

    # Act
    ulang_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    assert ulang_id != gagal_id
    lama, _ = _segarkan(db_session, gagal_id)
    assert lama.status == GradingRunStatus.FAILED
    baris = _riwayat(as_role, sesi.id)
    assert [b["grading_run_id"] for b in baris] == [str(ulang_id), str(gagal_id)]
    assert [b["kind"] for b in baris] == ["regrade", "initial"]
    assert [b["error_code"] for b in baris] == [None, "judge_unavailable"]
    assert [b["status"] for b in baris] == ["completed", "failed"]
    assert db_session.get(GradingRun, ulang_id).kind == GradingRunKind.REGRADE


def test_coba_nilai_ulang_saat_run_masih_aktif_ditolak_409(as_role, db_session, sesi):  # noqa: F811
    """Negatif: satu sesi satu run aktif; klik kedua tidak membuat run baru."""
    # Arrange
    _antre(as_role, sesi.id)
    sebelum = db_session.scalar(select(func.count()).select_from(GradingRun))

    # Act
    res = as_role(Role.ADMIN).post(f"/measurements/{sesi.id}/grading-runs", json={})

    # Assert
    assert res.status_code == 409
    assert res.json()["code"] == "GRADING_IN_PROGRESS"
    assert db_session.scalar(select(func.count()).select_from(GradingRun)) == sebelum


def test_perkiraan_biaya_untuk_coba_ulang_tidak_membuat_run(
    as_role,
    db_session,
    sesi,  # noqa: F811
    penilai,  # noqa: F811
    monkeypatch,
):
    """Positif: estimasi yang sama dengan penilaian pertama; tidak ada run atau panggilan judge."""
    # Arrange
    _gagal_dibangun(monkeypatch)
    _nilai(as_role, db_session, sesi.id)
    sebelum = db_session.scalar(select(func.count()).select_from(GradingRun))
    penilai.a.price_input_usd_per_mtok = Decimal("1")
    penilai.a.price_output_usd_per_mtok = Decimal("2")
    penilai.a.monthly_budget_idr = Decimal("1000")
    db_session.commit()
    monkeypatch.setitem(app.dependency_overrides, get_token_counter, lambda: _Counter())
    monkeypatch.setitem(
        app.dependency_overrides,
        get_exchange_rate,
        lambda: ConfiguredExchangeRate(Decimal("16000")),
    )

    # Act
    res = as_role(Role.ADMIN).post(f"/measurements/{sesi.id}/grade/estimate", json={})

    # Assert
    assert res.status_code == 200, res.text
    assert res.json()["attempts_to_grade"] == 2
    assert db_session.scalar(select(func.count()).select_from(GradingRun)) == sebelum
