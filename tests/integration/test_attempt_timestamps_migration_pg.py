"""SCRUM-259: migrasi b3c91d7a5e24 menambah waktu mulai/selesai percobaan.

Dua hal yang tidak terlihat dari test aplikasi:
- AC8: percobaan lama tidak diisi ulang, jadi setelah upgrade kedua kolom tetap NULL.
- downgrade() menolak jalan (RuntimeError) kalau sudah ada percobaan yang punya waktu,
  supaya data tidak hilang diam-diam; kalau semua NULL, downgrade lolos.

upgrade() dan downgrade() dijalankan langsung di koneksi tes lewat Operations.context,
jadi DDL ikut di-rollback fixture. Dilewati kalau TEST_POSTGRES_URL kosong.

Label: Positif = diterima, Negatif = ditolak, Edge = batas perilaku.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.measurement_pg import buat_admin, buat_item_snapshot, buat_produk, buat_sesi

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "b3c91d7a5e24_attempt_timestamps.py"
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

PESAN_TOLAK = "Cannot downgrade while attempt timestamps exist"


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
def percobaan(conn):
    """Buat percobaan otomatis tanpa waktu; kolom waktu belum diisi siapa pun."""
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(
        conn, buat_produk(conn, admin_id), admin_id, session_type="automatic", repetitions=1
    )
    item_id = buat_item_snapshot(conn, sesi_id, admin_id)
    return conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :sesi, :item, 1, CAST('automatic' AS attempt_source_enum)) "
            "RETURNING id"
        ),
        {"id": uuid.uuid4(), "sesi": sesi_id, "item": item_id},
    ).scalar_one()


def _jalankan(conn, arah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_259_attempt_timestamps", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, arah)()


def _kolom_waktu(conn) -> set[str]:
    return {
        baris[0]
        for baris in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'attempts' AND column_name IN ('started_at', 'finished_at')"
            )
        )
    }


def _waktu(conn, percobaan_id: uuid.UUID):
    return conn.execute(
        text("SELECT started_at, finished_at FROM attempts WHERE id = :id"),
        {"id": percobaan_id},
    ).one()


def test_upgrade_membiarkan_percobaan_lama_tanpa_waktu(conn, percobaan):
    """Positif (AC8): percobaan yang ada sebelum upgrade tetap NULL, tidak diisi ulang."""
    # Arrange: keadaan sebelum revisi ini, dengan satu percobaan lama
    _jalankan(conn, "downgrade")
    assert _kolom_waktu(conn) == set()

    # Act
    _jalankan(conn, "upgrade")

    # Assert
    assert _kolom_waktu(conn) == {"started_at", "finished_at"}
    assert tuple(_waktu(conn, percobaan)) == (None, None)


@pytest.mark.parametrize(
    "kolom_terisi",
    [
        pytest.param("started_at = now()", id="hanya-started_at"),
        pytest.param("started_at = now(), finished_at = now()", id="started_at-dan-finished_at"),
    ],
)
def test_downgrade_ditolak_selama_ada_percobaan_yang_punya_waktu(conn, percobaan, kolom_terisi):
    """Negatif: satu percobaan dengan waktu saja cukup untuk menolak; kolom tidak dihapus."""
    # Arrange
    conn.execute(text(f"UPDATE attempts SET {kolom_terisi} WHERE id = :id"), {"id": percobaan})

    # Act
    with pytest.raises(RuntimeError) as galat, conn.begin_nested():
        _jalankan(conn, "downgrade")

    # Assert
    assert str(galat.value) == PESAN_TOLAK
    assert _kolom_waktu(conn) == {"started_at", "finished_at"}


def test_downgrade_lolos_dan_menghapus_kolom_kalau_semua_waktu_kosong(conn, percobaan):
    """Positif: tanpa data waktu, downgrade menghapus kedua kolom; upgrade memasangnya lagi."""
    # Arrange: percobaan dari fixture tidak punya waktu

    # Act
    _jalankan(conn, "downgrade")
    sesudah_turun = _kolom_waktu(conn)
    _jalankan(conn, "upgrade")

    # Assert
    assert sesudah_turun == set()
    assert _kolom_waktu(conn) == {"started_at", "finished_at"}


def test_constraint_menolak_finished_at_tanpa_started_at(conn, percobaan):
    """Edge: CHECK finished_at IS NULL OR started_at IS NOT NULL dipasang oleh upgrade."""
    # Arrange: percobaan dari fixture belum punya started_at

    # Act
    with pytest.raises(IntegrityError) as galat, conn.begin_nested():
        conn.execute(
            text("UPDATE attempts SET finished_at = now() WHERE id = :id"), {"id": percobaan}
        )

    # Assert
    assert "ck_attempts_finished_needs_started" in str(galat.value)
