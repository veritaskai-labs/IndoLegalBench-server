"""SCRUM-214: migrasi c1fd2ffcc735 menambah paused dan cancelled, dan bisa dibalik dengan aman.

downgrade() dijalankan langsung di koneksi tes lewat Operations.context, jadi semua
DDL ikut di-rollback fixture. Sesi tidak bisa dihapus (trigger SCRUM-205), maka data
uji tidak boleh ter-commit: sesi paused yang tertinggal akan membuat tes downgrade
lain gagal. Dilewati kalau TEST_POSTGRES_URL kosong.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.measurement_pg import buat_admin, buat_produk, buat_sesi, ubah_status

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "c1fd2ffcc735_session_status_paused_cancelled.py"
)

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


def _downgrade(conn) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_214", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        modul.downgrade()


def _nilai_enum(conn) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'measurement_session_status_enum' ORDER BY e.enumsortorder"
            )
        ).scalars()
    )


def test_upgrade_menambah_paused_dan_cancelled(conn):
    """Positif: setelah head, enum status sesi berisi tujuh status D4."""
    # Assert
    assert _nilai_enum(conn) == [
        "queued",
        "running",
        "finished",
        "finished_with_failures",
        "failed",
        "paused",
        "cancelled",
    ]


@pytest.mark.parametrize("status", ["paused", "cancelled"])
def test_downgrade_menolak_selama_ada_sesi_berstatus_baru(conn, status):
    """Negatif: rollback tidak menebak status pengganti, karena itu menulis ulang riwayat."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, status)

    # Act / Assert
    with pytest.raises(RuntimeError, match="paused or cancelled"):
        _downgrade(conn)


def test_downgrade_tanpa_sesi_berstatus_baru_mengembalikan_lima_status(conn):
    """Positif: tipe dibangun ulang, default, CHECK, dan index satu-running kembali."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, "failed")

    # Act
    _downgrade(conn)

    # Assert
    assert _nilai_enum(conn) == [
        "queued",
        "running",
        "finished",
        "finished_with_failures",
        "failed",
    ]
    assert (
        conn.execute(
            text("SELECT status::text FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
        ).scalar_one()
        == "failed"
    )
    default = conn.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'measurement_sessions' AND column_name = 'status'"
        )
    ).scalar_one()
    assert default.startswith("'queued'")
    assert (
        conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'ck_measurement_sessions_running_not_archived'"
            )
        ).scalar_one()
        == 1
    )
    assert (
        conn.execute(
            text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE indexname = 'uq_measurement_sessions_one_running_automatic'"
            )
        ).scalar_one()
        == 1
    )
