"""SCRUM-284: satu run aktif per sesi dan tabel progres, di PostgreSQL. SCRUM-78 (K-13).

Index uq_grading_runs_one_active menjaga dua permintaan "Nilai" yang lolos cek aplikasi
bersamaan: hanya satu run queued atau running per sesi. Migrasi e5b1c7d93a20 bisa dibalik.

Dilewati kalau TEST_POSTGRES_URL kosong.

Label: Positif = diterima, Negatif = ditolak database, Edge = batas aturan.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.grading_pg import buat_run, buat_versi_aktif, selesaikan_run
from tests.measurement_pg import (
    CHECK_VIOLATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_produk,
    buat_sesi,
    galat,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
ROOT = Path(__file__).resolve().parents[2]
BERKAS = ROOT / "migrations" / "versions" / "e5b1c7d93a20_grading_run_progress.py"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            yield koneksi
        finally:
            transaksi.rollback()
    engine.dispose()


@pytest.fixture
def siap(conn):
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    versi_id = buat_versi_aktif(conn, admin_id)
    return admin_id, sesi_id, versi_id


def _jalankan(conn, langkah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_progres_penilaian", BERKAS)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, langkah)()


def _ada_tabel(conn, nama: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:nama) IS NOT NULL"), {"nama": nama}).scalar_one()


def test_run_aktif_kedua_untuk_sesi_yang_sama_ditolak(conn, siap):
    """Negatif: dua run queued untuk satu sesi melanggar index unik parsial."""
    # Arrange
    admin_id, sesi_id, versi_id = siap
    buat_run(conn, sesi_id, versi_id, admin_id)

    # Act
    kesalahan = galat(conn, lambda: buat_run(conn, sesi_id, versi_id, admin_id, kind="regrade"))

    # Assert
    assert kesalahan.sqlstate == UNIQUE_VIOLATION


def test_run_baru_boleh_setelah_run_sebelumnya_selesai(conn, siap):
    """Positif: index hanya mengikat run queued/running; nilai ulang setelah selesai tetap jalan."""
    # Arrange
    admin_id, sesi_id, versi_id = siap
    pertama = buat_run(conn, sesi_id, versi_id, admin_id)
    selesaikan_run(conn, pertama)

    # Act
    kedua = buat_run(conn, sesi_id, versi_id, admin_id, kind="regrade")

    # Assert
    jumlah = conn.execute(
        text("SELECT count(*) FROM grading_runs WHERE session_id = :s"), {"s": sesi_id}
    ).scalar_one()
    assert kedua is not None
    assert jumlah == 2


def test_progres_tidak_boleh_melebihi_total(conn, siap):
    """Negatif: attempts_graded di luar 0..attempts_total ditolak check constraint."""
    # Arrange
    admin_id, sesi_id, versi_id = siap
    run_id = buat_run(conn, sesi_id, versi_id, admin_id)

    # Act
    kesalahan = galat(
        conn,
        lambda: conn.execute(
            text(
                "INSERT INTO grading_run_progress (grading_run_id, attempts_total, attempts_graded) "
                "VALUES (:r, 2, 3)"
            ),
            {"r": run_id},
        ),
    )

    # Assert
    assert kesalahan.sqlstate == CHECK_VIOLATION


def test_migrasi_bisa_dibalik_lalu_dipasang_lagi(conn, siap):
    """Edge: downgrade membuang tabel progres dan index; upgrade memasangnya lagi."""
    # Act
    _jalankan(conn, "downgrade")
    tanpa_tabel = not _ada_tabel(conn, "grading_run_progress")
    tanpa_index = not _ada_tabel(conn, "uq_grading_runs_one_active")
    _jalankan(conn, "upgrade")

    # Assert
    assert tanpa_tabel and tanpa_index
    assert _ada_tabel(conn, "grading_run_progress")
    assert _ada_tabel(conn, "uq_grading_runs_one_active")
