"""Worker penilaian di latar belakang. SCRUM-78 SCRUM-284 bagian 2 (K-13).

Worker mengambil run queued paling lama, memanggil judge per percobaan sambil mencatat progres
dan heartbeat, lalu menyimpan hasilnya ke run yang sama. Run yang heartbeat-nya basi (server
mati di tengah penilaian) ditandai gagal GRADING_INTERRUPTED, tidak macet selamanya.

Isolasi: judge tiruan lewat build_judge (monkeypatch dari fixture pasang_penilai), kurs tetap
ConfiguredExchangeRate, dan waktu disuntikkan lewat argumen now supaya tidak ada tidur sungguhan.

Label: Positif = alur yang diharapkan, Negatif = run gagal dengan alasan, Edge = batas waktu.
"""

import signal
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import grading_worker as entry
from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunProgress,
    GradingRunStatus,
)
from app.modules.runs import grading_worker
from app.modules.runs.exchange_rate import ConfiguredExchangeRate
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.shared.security import Role
from tests.modules.runs.test_grading_runs_per_judge import (  # noqa: F401  (fixture)
    _PenilaiBerversi,
    pasang_penilai,
    pembuat,
    penilai,
    produk,
)
from tests.modules.runs.test_session_grade import _MockJudgeAsker, _setup_sesi_lengkap

KURS = ConfiguredExchangeRate(Decimal("16000"))
T0 = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)


@pytest.fixture
def sesi(db_session, produk, pembuat, penilai, pasang_penilai):  # noqa: F811
    item = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(item)
    return item


def _antre(as_role, sesi_id: uuid.UUID, **body) -> uuid.UUID:
    res = as_role(Role.ADMIN).post(f"/measurements/{sesi_id}/grading-runs", json=body)
    assert res.status_code == 202, res.text
    return uuid.UUID(res.json()["grading_run_id"])


def _segarkan(db, run_id: uuid.UUID) -> tuple[GradingRun, GradingRunProgress]:
    db.expire_all()
    return db.get(GradingRun, run_id), db.get(GradingRunProgress, run_id)


# --- Mengambil run --------------------------------------------------------------------


def test_ambil_run_antre_menjadi_running_dengan_heartbeat(as_role, db_session, sesi):
    """Positif: run queued diambil, statusnya running, dan waktu mulai serta heartbeat dicatat."""
    # Arrange
    run_id = _antre(as_role, sesi.id)

    # Act
    diambil = grading_worker.claim_next_run(db_session, now=T0)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert diambil == run_id
    assert run.status == GradingRunStatus.RUNNING
    assert progres.started_at.replace(tzinfo=UTC) == T0
    assert progres.heartbeat_at.replace(tzinfo=UTC) == T0


def test_tanpa_run_antre_tidak_ada_yang_diambil(db_session, sesi):
    """Edge: antrean kosong."""
    # Act + Assert
    assert grading_worker.claim_next_run(db_session, now=T0) is None


def test_run_yang_sudah_diambil_tidak_diambil_lagi(as_role, db_session, sesi):
    """Negatif: run running bukan antrean lagi, jadi tidak dinilai dua kali."""
    # Arrange
    _antre(as_role, sesi.id)
    grading_worker.claim_next_run(db_session, now=T0)

    # Act + Assert
    assert grading_worker.claim_next_run(db_session, now=T0) is None


# --- Menjalankan run ------------------------------------------------------------------


def test_run_dinilai_lengkap_dan_menjadi_hasil_terbaru(as_role, db_session, sesi, penilai):  # noqa: F811
    """Positif: K-13, hasil disimpan ke run yang sama, dengan judge pilihan Admin, 100 persen."""
    # Arrange
    run_id = _antre(as_role, sesi.id, judge_provider_id=str(penilai.b.id))
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FINISHED
    assert run.is_latest is True
    assert progres.attempts_graded == progres.attempts_total
    assert progres.error_code is None
    admin = as_role(Role.ADMIN)
    hasil = admin.get(f"/measurements/{sesi.id}/results").json()
    assert hasil["grading_run_id"] == str(run_id)
    assert hasil["judge"]["name"] == "Penilai B"
    assert admin.get(f"/measurements/{sesi.id}/grading-progress").json()["percent"] == 100


def test_progres_bertambah_selama_judge_berjalan(as_role, db_session, sesi, pasang_penilai):  # noqa: F811
    """Positif: progress bar bergerak; tiap panggilan judge melihat jumlah yang sudah tersimpan."""
    # Arrange
    terlihat: list[int] = []
    run_id = _antre(as_role, sesi.id)

    class _PenilaiPengamat(_PenilaiBerversi):
        def ask(self, prompt: str):
            terlihat.append(
                db_session.scalar(
                    select(GradingRunProgress.attempts_graded).where(
                        GradingRunProgress.grading_run_id == run_id
                    )
                )
            )
            return super().ask(prompt)

    pasang_penilai(sesi, asker_a=_PenilaiPengamat("model-a-2026"))
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    _, progres = _segarkan(db_session, run_id)
    assert terlihat[0] == 0
    assert terlihat == sorted(terlihat)
    assert terlihat[-1] > 0
    assert progres.attempts_graded == progres.attempts_total


def test_judge_gagal_semua_run_gagal_tanpa_menggeser_hasil(
    as_role,
    db_session,
    sesi,
    pasang_penilai,  # noqa: F811
):
    """Negatif: tanpa satu pun skor run berakhir failed GRADING_ERROR dan bukan hasil terbaru."""
    # Arrange
    run_id = _antre(as_role, sesi.id)
    pasang_penilai(sesi, asker_a=_MockJudgeAsker(raise_exc=True))
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert run.is_latest is False
    assert progres.error_code == "GRADING_ERROR"
    assert progres.attempts_graded == progres.attempts_total


def test_sesi_diarsipkan_saat_dinilai_run_gagal_tanpa_hasil(
    as_role,
    db_session,
    sesi,
    pembuat,  # noqa: F811
    pasang_penilai,  # noqa: F811
):
    """Negatif: arsip di tengah penilaian; hasil tidak disimpan dan alasannya tercatat."""
    # Arrange
    run_id = _antre(as_role, sesi.id)

    class _PengarsipSaatMenilai(_PenilaiBerversi):
        def ask(self, prompt: str):
            baris = db_session.get(MeasurementSession, sesi.id)
            baris.archived_at, baris.archived_by = T0, pembuat.id
            db_session.commit()
            return super().ask(prompt)

    pasang_penilai(sesi, asker_a=_PengarsipSaatMenilai("model-a-2026"))
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "SESSION_ARCHIVED"
    hasil = db_session.scalar(
        select(func.count())
        .select_from(GradingResult)
        .where(GradingResult.grading_run_id == run_id)
    )
    assert hasil == 0


def test_galat_tak_terduga_run_gagal_grading_error(as_role, db_session, sesi, monkeypatch, caplog):
    """Negatif: galat lain tidak membuat run macet; pesan galat tidak ikut tercatat (bisa memuat rahasia)."""
    # Arrange
    run_id = _antre(as_role, sesi.id)

    def meledak(*args, **kwargs):
        raise RuntimeError("rahasia-kredensial")

    monkeypatch.setattr(grading_worker, "grade_session_data", meledak)
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "GRADING_ERROR"
    assert "rahasia-kredensial" not in caplog.text


# --- Pemulihan --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lewat_detik", "terputus"),
    [(299, False), (300, True), (3600, True)],
)
def test_run_berjalan_dengan_heartbeat_basi_ditandai_terputus(
    as_role, db_session, sesi, lewat_detik, terputus
):
    """Edge: server mati di tengah penilaian; setelah 300 detik tanpa heartbeat run ditandai gagal."""
    # Arrange
    run_id = _antre(as_role, sesi.id)
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    ditandai = grading_worker.fail_stale_runs(db_session, now=T0 + timedelta(seconds=lewat_detik))

    # Assert
    run, progres = _segarkan(db_session, run_id)
    if terputus:
        assert ditandai == [run_id]
        assert run.status == GradingRunStatus.FAILED
        assert run.finished_at is not None
        assert progres.error_code == "GRADING_INTERRUPTED"
    else:
        assert ditandai == []
        assert run.status == GradingRunStatus.RUNNING


def test_run_antre_tidak_dianggap_basi(as_role, db_session, sesi):
    """Edge: run yang belum diambil worker menunggu, tidak ditandai terputus."""
    # Arrange
    run_id = _antre(as_role, sesi.id)

    # Act
    ditandai = grading_worker.fail_stale_runs(db_session, now=T0 + timedelta(days=1))

    # Assert
    run, _ = _segarkan(db_session, run_id)
    assert ditandai == []
    assert run.status == GradingRunStatus.QUEUED


# --- Satu putaran dan loop worker -------------------------------------------------------


def test_satu_putaran_menilai_run_antre(as_role, db_session, sesi):
    """Positif: run_once mengambil dan menilai satu run, lalu melapor ada pekerjaan."""
    # Arrange
    run_id = _antre(as_role, sesi.id)

    # Act
    sibuk = grading_worker.run_once(db_session, now=T0, rates=KURS)

    # Assert
    run, _ = _segarkan(db_session, run_id)
    assert sibuk is True
    assert run.status == GradingRunStatus.FINISHED
    assert grading_worker.run_once(db_session, now=T0, rates=KURS) is False


def test_loop_worker_memulihkan_lalu_menilai_sampai_dihentikan(db_session):
    """Positif: tiap putaran memulihkan run basi lalu menilai; berhenti saat SIGTERM (stop di-set)."""
    # Arrange
    stop = threading.Event()
    urutan: list[str] = []

    @contextmanager
    def pabrik():
        yield db_session

    def pulihkan(db, *, now):
        urutan.append("pulihkan")
        return []

    def nilai(db, *, now, rates):
        urutan.append("nilai")
        stop.set()
        return True

    # Act
    entry.serve(stop, factory=pabrik, dispatch=nilai, recover=pulihkan, rates=KURS)

    # Assert
    assert urutan == ["pulihkan", "nilai"]


def test_loop_worker_tetap_hidup_saat_putaran_gagal(db_session, caplog):
    """Negatif: galat di satu putaran dicatat tanpa isinya, worker lanjut dan bisa dihentikan."""
    # Arrange
    stop = threading.Event()
    putaran = {"n": 0}

    @contextmanager
    def pabrik():
        yield db_session

    def nilai(db, *, now, rates):
        putaran["n"] += 1
        if putaran["n"] == 1:
            raise RuntimeError("rahasia-kredensial")
        stop.set()
        return True

    # Act
    entry.serve(
        stop, factory=pabrik, dispatch=nilai, recover=lambda db, *, now: [], rates=KURS, idle=0
    )

    # Assert
    assert putaran["n"] == 2
    assert "rahasia-kredensial" not in caplog.text


def test_harga_judge_belum_lengkap_run_gagal_tanpa_memanggil_judge(
    as_role,
    db_session,
    sesi,
    penilai,  # noqa: F811
    monkeypatch,
):
    """Negatif: judge tanpa harga token tidak dipanggil (biayanya tidak bisa ditagih), run judge_pricing_missing."""
    # Arrange
    dipanggil: list[str] = []

    class _TercatatTanpaHarga(_PenilaiBerversi):
        def ask(self, prompt: str):
            dipanggil.append(prompt)
            return super().ask(prompt)

    ikatan = SimpleNamespace(
        judge_provider_id=penilai.a.id,
        asker=_TercatatTanpaHarga("model-a-2026"),
        price_input_usd_per_mtok=None,
        price_output_usd_per_mtok=None,
        monthly_budget_idr=Decimal("1000000"),
    )
    monkeypatch.setattr("app.modules.providers.judge_service.build_judge", lambda db, j: ikatan)
    run_id = _antre(as_role, sesi.id)
    grading_worker.claim_next_run(db_session, now=T0)

    # Act
    grading_worker.execute_run(db_session, run_id, now=T0, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert dipanggil == []
    assert run.status == GradingRunStatus.FAILED
    assert progres.error_code == "judge_pricing_missing"


def test_tanpa_waktu_suntikan_memakai_jam_sungguhan(as_role, db_session, sesi):
    """Edge: worker sungguhan tidak menyuntik waktu; heartbeat dan waktu selesai dari jam UTC."""
    # Arrange
    run_id = _antre(as_role, sesi.id)
    sebelum = datetime.now(UTC)

    # Act
    grading_worker.run_once(db_session, rates=KURS)

    # Assert
    run, progres = _segarkan(db_session, run_id)
    assert run.finished_at.replace(tzinfo=UTC) >= sebelum
    assert progres.heartbeat_at.replace(tzinfo=UTC) >= sebelum


def test_main_mendaftarkan_penghentian_rapi(monkeypatch):
    """Positif: SIGTERM dan SIGINT menghentikan loop dengan rapi (deploy memberi waktu tunggu)."""
    # Arrange
    penangan = {}
    monkeypatch.setattr(signal, "signal", lambda kunci, nilai: penangan.update({kunci: nilai}))

    def serve(stop, **kwargs):
        penangan[signal.SIGINT]()
        assert stop.is_set()
        stop.clear()
        penangan[signal.SIGTERM]()
        assert stop.is_set()

    monkeypatch.setattr(entry, "serve", serve)

    # Act + Assert
    entry.main()


def test_modul_bisa_dijalankan_dan_langsung_berhenti(monkeypatch):
    """Edge: python -m app.grading_worker tanpa pekerjaan saat sudah diminta berhenti."""
    # Arrange
    import runpy
    import sys

    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(threading, "Event", lambda: stop)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.delitem(sys.modules, "app.grading_worker")

    # Act + Assert
    runpy.run_module("app.grading_worker", run_name="__main__")
