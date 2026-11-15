"""Alasan grading run gagal tersimpan sebagai kode. SCRUM-78 SCRUM-289 (K-19).

Saat penilaian di latar belakang gagal, Admin harus tahu penyebabnya: judge belum dipilih, judge
tidak bisa dibangun, harga token kosong, anggaran habis, atau galat lain. Worker menyimpan satu
kode ke grading_run_progress.error_code. Teks galat tidak pernah disimpan atau ditampilkan
karena bisa memuat kredensial atau alamat endpoint (pola SafeProviderAdapter).

Isolasi: judge tiruan lewat build_judge (monkeypatch), kurs tetap, waktu disuntikkan.

Label: Positif = run selesai tanpa kode, Negatif = tiap penyebab gagal, Edge = gagal sebagian.
"""

import logging
import uuid
from decimal import Decimal

import pytest

from app.modules.grading.models import GradingRun, GradingRunStatus
from app.modules.runs import grading_worker
from app.modules.runs.session_grade import AttemptGradeInput, CaseGradeInput
from app.shared.security import Role
from tests.modules.runs.test_grading_runs_per_judge import (  # noqa: F401  (fixture)
    _ikatan,
    pasang_penilai,
    pembuat,
    penilai,
    produk,
)
from tests.modules.runs.test_grading_worker import KURS, T0, _antre, _segarkan, sesi  # noqa: F401
from tests.modules.runs.test_session_grade import _MockJudgeAsker

RAHASIA = "sk-rahasia-123"
ENDPOINT = "https://api.penyedia.test"


def _nilai(as_role, db_session, sesi_id) -> uuid.UUID:
    """Antre, ambil, lalu nilai satu run sampai worker selesai."""
    run_id = _antre(as_role, sesi_id)
    grading_worker.claim_next_run(db_session, now=T0)
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)
    return run_id


def _ikat(monkeypatch, pasangan, asker=None, **override) -> None:
    """build_judge mengembalikan ikatan Penilai A dengan harga atau anggaran yang diubah."""
    ikatan = _ikatan(pasangan.a.id, asker or _MockJudgeAsker(raise_exc=True))
    for nama, nilai in override.items():
        setattr(ikatan, nama, nilai)
    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", lambda db, j: ikatan)


def test_run_yang_dinilai_lengkap_tidak_punya_kode_gagal(as_role, db_session, sesi):  # noqa: F811
    """Positif: run selesai membawa error_code kosong."""
    # Act
    run_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FINISHED
    assert progres.error_code is None


def test_judge_gagal_dibangun_menyimpan_judge_unavailable(
    as_role,
    db_session,
    sesi,  # noqa: F811
    monkeypatch,
    caplog,
):
    """Negatif: judge tidak bisa dibangun; kodenya judge_unavailable dan teks galat tidak bocor."""

    # Arrange
    def gagal_dibangun(db, judge_id):
        raise RuntimeError(f"{RAHASIA} {ENDPOINT}")

    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", gagal_dibangun)

    # Act
    with caplog.at_level(logging.DEBUG):
        run_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "judge_unavailable"
    assert RAHASIA not in caplog.text


def test_run_tanpa_judge_menyimpan_judge_not_configured(as_role, db_session, sesi):  # noqa: F811
    """Negatif: sesi lama tanpa judge dan run tanpa pilihan Admin."""
    # Arrange
    run_id = _antre(as_role, sesi.id)
    db_session.get(GradingRun, run_id).judge_provider_id = None
    sesi.judge_provider_id = None
    db_session.commit()
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "judge_not_configured"


@pytest.mark.parametrize("kosong", ["price_input_usd_per_mtok", "price_output_usd_per_mtok"])
def test_harga_token_judge_kosong_menyimpan_judge_pricing_missing(
    as_role,
    db_session,
    sesi,  # noqa: F811
    penilai,  # noqa: F811
    monkeypatch,
    kosong,
):
    """Negatif: harga masuk atau keluar kosong, judge tidak dipanggil, kodenya sendiri."""
    # Arrange
    asker = _MockJudgeAsker(raise_exc=True)
    _ikat(monkeypatch, penilai, asker, **{kosong: None})

    # Act
    run_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "judge_pricing_missing"


def test_anggaran_bulanan_habis_menyimpan_judge_budget_exhausted(
    as_role,
    db_session,
    sesi,  # noqa: F811
    penilai,  # noqa: F811
    monkeypatch,
):
    """Negatif: sisa anggaran 0 sejak awal, tidak ada percobaan yang dinilai."""
    # Arrange
    _ikat(monkeypatch, penilai, monthly_budget_idr=Decimal("0"))

    # Act
    run_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "judge_budget_exhausted"


def test_anggaran_habis_di_tengah_run_tetap_selesai_tanpa_kode_gagal(
    as_role,
    db_session,
    sesi,  # noqa: F811
    penilai,  # noqa: F811
    monkeypatch,
):
    """Edge: satu percobaan sempat dinilai, sisanya terganggu; run tetap selesai tanpa kode gagal."""
    # Arrange
    from tests.modules.runs.test_grading_runs_per_judge import _PenilaiBerversi

    _ikat(
        monkeypatch,
        penilai,
        _PenilaiBerversi("model-a-2026"),
        monthly_budget_idr=Decimal("0.01"),
    )

    # Act
    run_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FINISHED
    assert progres.error_code is None
    hasil = as_role(Role.ADMIN).get(f"/measurements/{sesi.id}/grading-runs/{run_id}").json()
    terganggu = [
        percobaan
        for kasus in hasil["cases"]
        for percobaan in kasus["attempts"]
        if percobaan["is_technical_disruption"]
    ]
    assert len(terganggu) == 1


def test_galat_judge_tak_dikenal_menyimpan_grading_error_tanpa_teksnya(
    as_role,
    db_session,
    sesi,  # noqa: F811
    penilai,  # noqa: F811
    monkeypatch,
    caplog,
):
    """Negatif: judge meledak dengan teks berisi kredensial; run gagal GRADING_ERROR dan bersih."""

    # Arrange
    class _PenilaiMeledak:
        def ask(self, prompt):
            raise RuntimeError(f"401 Bearer {RAHASIA} dari {ENDPOINT}")

    _ikat(monkeypatch, penilai, _PenilaiMeledak())

    # Act
    with caplog.at_level(logging.DEBUG):
        run_id = _nilai(as_role, db_session, sesi.id)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "GRADING_ERROR"
    assert RAHASIA not in caplog.text
    hasil = as_role(Role.ADMIN).get(f"/measurements/{sesi.id}/grading-runs/{run_id}")
    assert hasil.status_code == 200
    assert RAHASIA not in hasil.text
    assert ENDPOINT not in hasil.text


def test_percobaan_yang_judge_nya_meledak_menyimpan_kode_tetap_bukan_teks_galat():
    """Negatif: judge_error percobaan adalah kode tetap, sebab ia tampil sebagai penjelasan."""
    # Arrange
    from app.modules.runs.models import AttemptStatus
    from app.modules.runs.session_grade import _execute_judge_for_attempt

    percobaan = AttemptGradeInput(
        attempt_id=uuid.uuid4(),
        repetition_no=1,
        status=AttemptStatus.SUCCEEDED,
        answer_text="Jawaban provider.",
    )
    kasus = CaseGradeInput(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code="DEV-01",
        title="Kasus",
        question="Apa akibat hukumnya?",
        answer_criteria={"must_contain": ["badan hukum"], "conclusion": "sah"},
        attempts=[percobaan],
    )

    # Act
    _execute_judge_for_attempt(_MockJudgeAsker(raise_exc=True), percobaan, kasus)

    # Assert
    assert percobaan.judge_error == "judge_error"
    assert "API timeout simulation" not in percobaan.judge_error
