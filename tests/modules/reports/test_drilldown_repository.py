"""Query penelusuran angka laporan di SQLite (PBI-16 SCRUM-326, diagram D6 langkah 2-7).

Yang diuji: satu angka laporan dan sesinya hanya terbaca lewat laporannya sendiri, dan
run penilaian yang dipakai penelusuran adalah run yang dipakai laporan saat dibuat (D1:
hasil beku), bukan run terbaru setelah nilai ulang.

Data dibuat dengan tests/reports_factory.py milik SCRUM-300. SQLite tidak menyalakan
trigger, jadi status run dan waktunya boleh diisi langsung.

Label: Positif = baris yang harus terbaca, Negatif = baris yang harus diabaikan,
Edge = batas waktu dan urutan.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.grading.models import GradingRun, GradingRunKind, GradingRunStatus
from app.modules.reports import repository
from tests import reports_factory as pabrik

LAPORAN_DIBUAT = datetime(2026, 12, 1, 10, tzinfo=UTC)


@pytest.fixture
def laporan_satu_sesi(db_session):
    """Satu laporan dengan satu sesi dan satu angka, plus laporan lain sebagai pengecoh."""
    admin = pabrik.buat_admin(db_session)
    rubrik = pabrik.buat_rubrik(db_session, admin)
    sesi = pabrik.buat_sesi(db_session, admin, "Justisio")
    report = pabrik.laporan(admin, rubrik, case_basis_count=1)
    hasil = pabrik.hasil(report, sesi)
    repository.add_report(
        db_session, report, [pabrik.sesi_laporan(report, sesi)], [hasil], [pabrik.dasar(report)]
    )
    lain = pabrik.laporan(admin, rubrik, case_basis_count=1)
    repository.add_report(
        db_session,
        lain,
        [pabrik.sesi_laporan(lain, sesi)],
        [pabrik.hasil(lain, sesi)],
        [pabrik.dasar(lain)],
    )
    return admin, rubrik, sesi, report, hasil, lain


def _run(db, admin, rubrik, sesi, *, selesai, status=GradingRunStatus.FINISHED, **ubah):
    """Satu run penilaian sesi yang selesai pada waktu `selesai` (None = belum selesai)."""
    run = GradingRun(
        session_id=sesi.id,
        rubric_version_id=rubrik.id,
        kind=GradingRunKind.INITIAL,
        status=status,
        is_latest=False,
        triggered_by=admin.id,
        triggered_at=(selesai or LAPORAN_DIBUAT) - timedelta(minutes=5),
        finished_at=selesai,
        **ubah,
    )
    db.add(run)
    db.flush()
    return run


# --- get_result dan get_report_session -----------------------------------------


def test_get_result_membaca_angka_milik_laporannya(db_session, laporan_satu_sesi):
    """Positif: angka yang diklik dibaca dari laporan yang sedang dibuka."""
    # Arrange
    *_, report, hasil, _ = laporan_satu_sesi

    # Act
    baris = repository.get_result(db_session, report.id, hasil.id)

    # Assert
    assert baris is hasil


def test_get_result_dari_laporan_lain_tidak_terbaca(db_session, laporan_satu_sesi):
    """Negatif: id angka laporan lain tidak bisa dipakai lewat laporan yang boleh dibaca.

    Tanpa batas ini, pengguna yang boleh membaca laporan A bisa menelusuri angka laporan B
    yang tersimpan milik orang lain hanya dengan menukar id di URL.
    """
    # Arrange
    *_, report, hasil, lain = laporan_satu_sesi

    # Act
    tertukar = repository.get_result(db_session, lain.id, hasil.id)
    acak = repository.get_result(db_session, report.id, uuid.uuid4())

    # Assert
    assert (tertukar, acak) == (None, None)


def test_get_report_session_hanya_sesi_dalam_laporan(db_session, laporan_satu_sesi):
    """Positif dan negatif: label beku sesi terbaca; sesi di luar laporan tidak."""
    # Arrange
    _, _, sesi, report, _, _ = laporan_satu_sesi

    # Act
    ada = repository.get_report_session(db_session, report.id, sesi.id)
    tidak_ada = repository.get_report_session(db_session, report.id, uuid.uuid4())

    # Assert
    assert ada.product_label == "Produk A"
    assert tidak_ada is None


# --- grading_run_at ---------------------------------------------------------------


def test_run_yang_dipakai_laporan_bukan_nilai_ulang_sesudahnya(db_session, laporan_satu_sesi):
    """Positif: nilai ulang setelah laporan dibuat tidak mengubah skor di penelusuran (D1)."""
    # Arrange
    admin, rubrik, sesi, *_ = laporan_satu_sesi
    lama = _run(db_session, admin, rubrik, sesi, selesai=LAPORAN_DIBUAT - timedelta(days=2))
    dipakai = _run(db_session, admin, rubrik, sesi, selesai=LAPORAN_DIBUAT - timedelta(hours=1))
    _run(db_session, admin, rubrik, sesi, selesai=LAPORAN_DIBUAT + timedelta(days=1))

    # Act
    run_id = repository.grading_run_at(db_session, sesi.id, LAPORAN_DIBUAT)

    # Assert
    assert run_id == dipakai.id != lama.id


@pytest.mark.parametrize(
    "status", [GradingRunStatus.FAILED, GradingRunStatus.QUEUED, GradingRunStatus.RUNNING]
)
def test_run_gagal_atau_belum_selesai_tidak_dipakai(db_session, laporan_satu_sesi, status):
    """Negatif: SCRUM-313 hanya memakai run selesai, jadi nilai ulang yang gagal dilewati."""
    # Arrange
    admin, rubrik, sesi, *_ = laporan_satu_sesi
    bagus = _run(db_session, admin, rubrik, sesi, selesai=LAPORAN_DIBUAT - timedelta(days=1))
    _run(
        db_session,
        admin,
        rubrik,
        sesi,
        selesai=None if status is not GradingRunStatus.FAILED else LAPORAN_DIBUAT,
        status=status,
    )

    # Act
    run_id = repository.grading_run_at(db_session, sesi.id, LAPORAN_DIBUAT)

    # Assert
    assert run_id == bagus.id


def test_run_selesai_tepat_saat_laporan_dibuat_ikut_dipakai(db_session, laporan_satu_sesi):
    """Edge: batasnya inklusif, karena laporan bisa dibuat di detik yang sama run selesai."""
    # Arrange
    admin, rubrik, sesi, *_ = laporan_satu_sesi
    tepat = _run(db_session, admin, rubrik, sesi, selesai=LAPORAN_DIBUAT)

    # Act
    run_id = repository.grading_run_at(db_session, sesi.id, LAPORAN_DIBUAT)

    # Assert
    assert run_id == tepat.id


def test_tanpa_run_sebelum_laporan_mengembalikan_none(db_session, laporan_satu_sesi):
    """Edge: data rusak (laporan tanpa run sebelumnya) dijawab None, bukan run masa depan."""
    # Arrange
    admin, rubrik, sesi, *_ = laporan_satu_sesi
    _run(db_session, admin, rubrik, sesi, selesai=LAPORAN_DIBUAT + timedelta(minutes=1))

    # Act
    run_id = repository.grading_run_at(db_session, sesi.id, LAPORAN_DIBUAT)

    # Assert
    assert run_id is None


def test_run_sesi_lain_tidak_ikut_terbaca(db_session, laporan_satu_sesi):
    """Negatif: run milik sesi lain tidak pernah dipakai untuk sesi ini."""
    # Arrange
    admin, rubrik, sesi, *_ = laporan_satu_sesi
    sesi_lain = pabrik.buat_sesi(db_session, admin, "Gemini")
    _run(db_session, admin, rubrik, sesi_lain, selesai=LAPORAN_DIBUAT - timedelta(hours=1))

    # Act
    run_id = repository.grading_run_at(db_session, sesi.id, LAPORAN_DIBUAT)

    # Assert
    assert run_id is None
