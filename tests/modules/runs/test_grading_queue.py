"""Penilaian di latar belakang: antre, satu run aktif, dan progres. SCRUM-78 SCRUM-284 (K-13).

Klien (UAT 9 Nov): setelah "Nilai" atau "Kirim untuk Dinilai", layar tertahan di pop-up sampai
penilaian selesai. POST /measurements/{id}/grading-runs kini hanya mencatat run berstatus queued
dan langsung membalas 202; worker penilaian (PR berikutnya) yang memanggil judge. Endpoint lama
POST /grade tetap ada sampai client pindah (SCRUM-285).

Label: Positif = alur yang diharapkan, Negatif = permintaan ditolak, Edge = batas aturan.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.modules.grading.models import GradingRun, GradingRunProgress, GradingRunStatus
from app.modules.runs.grading_queue import progress_percent
from app.modules.runs.models import Attempt, SessionStatus
from app.shared.security import Role
from tests.modules.runs.test_grading_runs_per_judge import (  # noqa: F401  (fixture)
    pasang_penilai,
    pembuat,
    penilai,
    produk,
)
from tests.modules.runs.test_session_grade import _setup_sesi_lengkap


def _antre(sesi_id: uuid.UUID) -> str:
    return f"/measurements/{sesi_id}/grading-runs"


def _progres(sesi_id: uuid.UUID) -> str:
    return f"/measurements/{sesi_id}/grading-progress"


def _jumlah_run(db) -> int:
    return db.scalar(select(func.count()).select_from(GradingRun))


@pytest.fixture
def sesi(db_session, produk, pembuat, penilai, pasang_penilai):  # noqa: F811
    """Sesi selesai yang punya judge A; build_judge mengembalikan asker tiruan."""
    item = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    pasang_penilai(item)
    return item


def test_antre_penilaian_membalas_202_tanpa_memanggil_judge(as_role, db_session, sesi, penilai):  # noqa: F811
    """Positif: K-13, run dicatat queued dengan judge pilihan Admin; judge belum dipanggil."""
    # Arrange
    admin = as_role(Role.ADMIN)

    # Act
    res = admin.post(_antre(sesi.id), json={"judge_provider_id": str(penilai.b.id)})

    # Assert
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "queued"
    run = db_session.get(GradingRun, uuid.UUID(body["grading_run_id"]))
    assert run.status == GradingRunStatus.QUEUED
    assert run.judge_provider_id == penilai.b.id
    assert run.kind.value == "initial"
    assert run.finished_at is None
    progres = db_session.get(GradingRunProgress, run.id)
    jumlah_percobaan = db_session.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.session_id == sesi.id)
    )
    assert progres.attempts_total == jumlah_percobaan
    assert progres.attempts_graded == 0
    assert progres.error_code is None


def test_antre_tanpa_judge_memakai_judge_sesi(as_role, db_session, sesi, penilai):  # noqa: F811
    """Edge: body kosong memakai judge sesi, sama seperti POST /grade."""
    # Act
    res = as_role(Role.ADMIN).post(_antre(sesi.id), json={})

    # Assert
    assert res.status_code == 202, res.text
    run = db_session.get(GradingRun, uuid.UUID(res.json()["grading_run_id"]))
    assert run.judge_provider_id == penilai.a.id


@pytest.mark.parametrize("ulang", ["grading-runs", "grade"])
def test_permintaan_kedua_saat_run_aktif_ditolak_409(as_role, db_session, sesi, ulang):
    """Negatif: satu sesi hanya punya satu run aktif, lewat endpoint baru maupun POST /grade."""
    # Arrange
    admin = as_role(Role.ADMIN)
    admin.post(_antre(sesi.id), json={})

    # Act
    res = admin.post(f"/measurements/{sesi.id}/{ulang}", json={})

    # Assert
    assert res.status_code == 409
    assert res.json()["code"] == "GRADING_IN_PROGRESS"
    assert _jumlah_run(db_session) == 1


def test_antre_tanpa_judge_sama_sekali_ditolak_422(as_role, db_session, sesi):
    """Negatif: K-14, tanpa judge di permintaan maupun sesi tidak ada run yang dicatat."""
    # Arrange
    sesi.judge_provider_id = None
    db_session.commit()

    # Act
    res = as_role(Role.ADMIN).post(_antre(sesi.id), json={})

    # Assert
    assert res.status_code == 422
    assert res.json()["code"] == "JUDGE_REQUIRED"
    assert _jumlah_run(db_session) == 0


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.CONTRIBUTOR, Role.DEVELOPER_AI])
def test_antre_selain_admin_ditolak_403(peran, as_role, db_session, sesi):
    """Negatif: hanya Admin yang memicu penilaian."""
    # Act
    res = as_role(peran).post(_antre(sesi.id), json={})

    # Assert
    assert res.status_code == 403
    assert _jumlah_run(db_session) == 0


def test_antre_sesi_tidak_ada_ditolak_404(as_role):
    """Negatif: sesi yang tidak dikenal."""
    # Act
    res = as_role(Role.ADMIN).post(_antre(uuid.uuid4()), json={})

    # Assert
    assert res.status_code == 404
    assert res.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_progres_sesi_belum_pernah_dinilai_null(as_role, sesi):
    """Edge: belum ada run, jadi tidak ada progres untuk ditampilkan."""
    # Act
    res = as_role(Role.ADMIN).get(_progres(sesi.id))

    # Assert
    assert res.status_code == 200
    assert res.json() is None


def test_progres_run_antre_nol_persen(as_role, db_session, sesi):
    """Positif: setelah refresh halaman, run yang masih antre tetap terbaca dengan progresnya."""
    # Arrange
    admin = as_role(Role.ADMIN)
    run_id = admin.post(_antre(sesi.id), json={}).json()["grading_run_id"]

    # Act
    res = admin.get(_progres(sesi.id))

    # Assert
    assert res.status_code == 200
    body = res.json()
    assert body["grading_run_id"] == run_id
    assert body["status"] == "queued"
    assert body["attempts_graded"] == 0
    assert body["attempts_total"] > 0
    assert body["percent"] == 0
    assert body["error_code"] is None


def test_progres_run_selesai_lewat_grade_lama_seratus_persen(as_role, sesi):
    """Edge: run dari POST /grade (tanpa baris progres) terbaca selesai 100 persen."""
    # Arrange
    admin = as_role(Role.ADMIN)
    admin.post(f"/measurements/{sesi.id}/grade", json={})

    # Act
    body = admin.get(_progres(sesi.id)).json()

    # Assert
    assert body["status"] == "finished"
    assert body["attempts_graded"] == body["attempts_total"]
    assert body["attempts_total"] > 0
    assert body["percent"] == 100


def test_progres_selain_admin_ditolak_403(as_role, sesi):
    """Negatif: progres penilaian mengikuti riwayat penilaian, hanya Admin."""
    # Act
    res = as_role(Role.VIEWER).get(_progres(sesi.id))

    # Assert
    assert res.status_code == 403


def test_progres_sesi_tidak_ada_ditolak_404(as_role):
    """Negatif: sesi yang tidak dikenal."""
    # Act
    res = as_role(Role.ADMIN).get(_progres(uuid.uuid4()))

    # Assert
    assert res.status_code == 404


def test_run_antre_belum_muncul_di_riwayat_hasil_dan_laporan(as_role, sesi):
    """Edge: run tanpa hasil bukan hasil penilaian; riwayat, hasil, dan Laporan tidak berubah."""
    # Arrange
    admin = as_role(Role.ADMIN)
    run_id = admin.post(_antre(sesi.id), json={}).json()["grading_run_id"]

    # Act
    riwayat = admin.get(f"/measurements/{sesi.id}/grading-runs").json()
    hasil = admin.get(f"/measurements/{sesi.id}/results")
    detail = admin.get(f"/measurements/{sesi.id}/grading-runs/{run_id}")
    laporan = admin.get("/results/sessions").json()

    # Assert
    assert riwayat == []
    assert hasil.status_code == 404
    assert detail.status_code == 404
    assert all(baris["grading_run_id"] != run_id for baris in laporan)


def test_dua_klik_bersamaan_ditolak_index_satu_run_aktif(as_role, db_session, sesi, monkeypatch):
    """Edge: dua permintaan lolos cek aplikasi bersamaan; index unik menolak yang kedua dengan 409."""
    # Arrange
    admin = as_role(Role.ADMIN)
    admin.post(_antre(sesi.id), json={})
    # Permintaan kedua dianggap sudah lewat cek sebelum run pertama tersimpan.
    monkeypatch.setattr(
        "app.modules.runs.session_grade.ensure_no_active_run", lambda db, session_id: None
    )

    # Act
    res = admin.post(_antre(sesi.id), json={})

    # Assert
    assert res.status_code == 409
    assert res.json()["code"] == "GRADING_IN_PROGRESS"
    assert _jumlah_run(db_session) == 1


@pytest.mark.parametrize(
    ("dinilai", "total", "selesai", "persen"),
    [
        (0, 0, False, 0),
        (0, 0, True, 100),
        (1, 3, False, 33),
        (2, 3, False, 66),
        (3, 3, True, 100),
        (5, 4, False, 100),
    ],
)
def test_persen_progres_dibulatkan_ke_bawah(dinilai, total, selesai, persen):
    """Edge: 100 persen hanya saat semua dinilai; sesi tanpa percobaan tidak membagi nol."""
    # Act + Assert
    assert progress_percent(dinilai, total, finished=selesai) == persen
