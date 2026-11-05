"""SCRUM-214: migrasi 9b526a16b704 menolak label dan alasan yang hanya berisi karakter kosong.

Catatan Adra di #89: CHECK lama memakai trim() biasa, yang hanya membuang spasi, jadi
tab, NBSP, atau zero-width saja lolos. Revisi ini mengganti kedua CHECK dengan daftar
karakter kosong yang sama dengan app.shared.text.

upgrade() dan downgrade() dijalankan langsung di koneksi tes lewat Operations.context,
jadi semua DDL ikut di-rollback fixture (pola test_session_status_migration_pg.py).
Dilewati kalau TEST_POSTGRES_URL kosong.

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
from sqlalchemy.exc import DBAPIError

from tests.measurement_pg import CHECK_VIOLATION, buat_admin, buat_produk, buat_sesi, ubah_status

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "9b526a16b704_blank_label_reason_check.py"
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


def _jalankan(conn, arah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_214_kosong", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, arah)()


def _label_diterima(conn, label: str) -> bool:
    admin_id = buat_admin(conn)
    savepoint = conn.begin_nested()
    try:
        buat_sesi(conn, buat_produk(conn, admin_id), admin_id, label=label)
    except DBAPIError as error:
        assert error.orig.sqlstate == CHECK_VIOLATION
        diterima = False
    else:
        diterima = True
    savepoint.rollback()
    return diterima


def test_head_menolak_label_zero_width(conn):
    """Positif: di head, label yang hanya zero-width ditolak database."""
    assert not _label_diterima(conn, "\u200b")


def test_downgrade_mengembalikan_check_trim_lama(conn):
    """Positif: setelah dibalik, CHECK lama kembali (zero-width lolos, spasi tetap ditolak)."""
    # Act
    _jalankan(conn, "downgrade")

    # Assert
    assert _label_diterima(conn, "\u200b")
    assert not _label_diterima(conn, "   ")


def test_upgrade_setelah_downgrade_menolak_lagi(conn):
    """Positif: upgrade dari revisi sebelumnya memasang CHECK baru."""
    # Arrange
    _jalankan(conn, "downgrade")

    # Act
    _jalankan(conn, "upgrade")

    # Assert
    assert not _label_diterima(conn, "\u00a0\t")


def test_upgrade_menolak_selama_ada_label_kosong(conn):
    """Negatif: upgrade tidak menebak label pengganti; Admin harus memberi nama dulu."""
    # Arrange
    _jalankan(conn, "downgrade")
    admin_id = buat_admin(conn)
    buat_sesi(conn, buat_produk(conn, admin_id), admin_id, label="\u200b\u200b")

    # Act / Assert
    with pytest.raises(RuntimeError, match="blank label"):
        _jalankan(conn, "upgrade")


def test_upgrade_menolak_selama_ada_alasan_terima_kosong(conn):
    """Negatif: alasan 'terima apa adanya' yang kosong juga harus dibereskan dulu."""
    # Arrange
    _jalankan(conn, "downgrade")
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, "finished_with_failures")
    conn.execute(
        text(
            "UPDATE measurement_sessions SET accepted_as_is_at = now(), "
            "accepted_as_is_by = :admin_id, accepted_as_is_reason = :alasan WHERE id = :id"
        ),
        {"id": sesi_id, "admin_id": admin_id, "alasan": "\u00a0"},
    )

    # Act / Assert
    with pytest.raises(RuntimeError, match="blank accept-as-is reason"):
        _jalankan(conn, "upgrade")
