"""Pembersihan snapshot manual lama yang bisa di-rollback. SCRUM-76 SCRUM-207.

PBI-11 AC7: "snapshot lama hasil pembuatan manual saat pengujian boleh
dihapus", lewat langkah migrasi yang bisa di-rollback (tiket SCRUM-207). D2:
tabel suite_snapshots Sprint 2 dipensiunkan. Upgrade memindahkan baris ke
tabel retired_* lalu mengosongkan tabel asal; downgrade mengembalikan semuanya.

Semua baris dibuat di dalam transaksi yang dibatalkan di akhir test.

Label: Positif = data dipindah atau kembali utuh, Edge = batas perilaku.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "e4b7c2d9a615_retire_manual_suite_snapshots.py"
)


def _muat_migrasi():
    assert MIGRASI.exists(), f"migrasi {MIGRASI.name} belum ada"
    spec = importlib.util.spec_from_file_location("migrasi_snapshot_manual", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def conn():
    """Koneksi di keadaan sebelum migrasi ini; semuanya dibatalkan di akhir test.

    Database uji biasanya sudah di head, jadi tabel retired_* sudah ada dan
    dikembalikan dulu supaya setiap test mulai dari keadaan Sprint 2.
    """
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            if _ada_tabel(koneksi, "retired_suite_snapshots"):
                _muat_migrasi().restore_manual_snapshots(koneksi)
            yield koneksi
        finally:
            transaksi.rollback()
    engine.dispose()


@pytest.fixture
def snapshot_lama(conn) -> uuid.UUID:
    """Satu snapshot manual Sprint 2 berisi satu kasus approved, seperti hasil uji coba."""
    user_id, suite_id, case_id, version_id, snapshot_id = (uuid.uuid4() for _ in range(5))
    kode = f"QA-207-{str(case_id)[:8]}"
    dasar = {
        "user": user_id,
        "suite": suite_id,
        "kasus": case_id,
        "versi": version_id,
        "snapshot": snapshot_id,
        "kode": kode,
        "email": f"qa-207-{user_id}@veritask.test",
        "nama": f"Suite {suite_id}",
    }
    for sql in (
        "INSERT INTO users (id, email, name, role) VALUES (:user, :email, 'QA 207', 'admin')",
        "INSERT INTO suites (id, name, status, created_by) VALUES (:suite, :nama, 'active', :user)",
        "INSERT INTO case_versions "
        "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
        "VALUES (:versi, :kasus, 1, 'approved', 'dev', CAST('{}' AS jsonb), :user, :kode)",
        "INSERT INTO cases (id, case_code, suite_id, created_by, updated_by, current_version_id) "
        "VALUES (:kasus, :kode, :suite, :user, :user, :versi)",
        "INSERT INTO suite_snapshots (id, suite_id, created_by) VALUES (:snapshot, :suite, :user)",
        "INSERT INTO suite_snapshot_items (id, snapshot_id, case_id, case_version_id, body) "
        "VALUES (gen_random_uuid(), :snapshot, :kasus, :versi, CAST('{\"a\": 1}' AS jsonb))",
    ):
        conn.execute(text(sql), dasar)
    return snapshot_id


def _jumlah(conn, tabel: str) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {tabel}")).scalar_one()


def _ada_tabel(conn, tabel: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": tabel}).scalar_one()


def test_upgrade_memindahkan_semua_snapshot_lama_lalu_mengosongkan_tabel_asal(conn, snapshot_lama):
    """Positif: AC7, tabel asal kosong dan setiap baris tersimpan di tabel retired_*."""
    # Arrange: tabel bersama test PG lain, jadi hitung semua baris yang ada
    snapshot = _jumlah(conn, "suite_snapshots")
    item = _jumlah(conn, "suite_snapshot_items")

    # Act
    dipindah = _muat_migrasi().retire_manual_snapshots(conn)

    # Assert
    assert dipindah == snapshot
    assert _jumlah(conn, "suite_snapshots") == 0
    assert _jumlah(conn, "suite_snapshot_items") == 0
    assert _jumlah(conn, "retired_suite_snapshots") == snapshot
    assert _jumlah(conn, "retired_suite_snapshot_items") == item
    simpanan = conn.execute(
        text("SELECT count(*) FROM retired_suite_snapshots WHERE id = :id"), {"id": snapshot_lama}
    ).scalar_one()
    assert simpanan == 1


def test_downgrade_mengembalikan_snapshot_dan_isinya_utuh(conn, snapshot_lama):
    """Positif: rollback mengembalikan baris yang sama, termasuk isi body, dan membuang tabel retired_*."""
    # Arrange
    migrasi = _muat_migrasi()
    sebelum = conn.execute(
        text("SELECT body FROM suite_snapshot_items WHERE snapshot_id = :id"), {"id": snapshot_lama}
    ).scalar_one()
    migrasi.retire_manual_snapshots(conn)

    # Act
    dikembalikan = migrasi.restore_manual_snapshots(conn)

    # Assert
    assert dikembalikan >= 1
    sesudah = conn.execute(
        text("SELECT body FROM suite_snapshot_items WHERE snapshot_id = :id"), {"id": snapshot_lama}
    ).scalar_one()
    assert sesudah == sebelum == {"a": 1}
    assert not _ada_tabel(conn, "retired_suite_snapshots")
    assert not _ada_tabel(conn, "retired_suite_snapshot_items")


def test_upgrade_tanpa_snapshot_lama_tetap_membuat_tabel_retired_kosong(conn):
    """Edge: lingkungan yang tidak pernah membuat snapshot manual tetap bisa di-upgrade dan di-rollback."""
    # Arrange
    conn.execute(text("DELETE FROM suite_snapshot_items"))
    conn.execute(text("DELETE FROM suite_snapshots"))
    migrasi = _muat_migrasi()

    # Act
    dipindah = migrasi.retire_manual_snapshots(conn)
    dikembalikan = migrasi.restore_manual_snapshots(conn)

    # Assert
    assert (dipindah, dikembalikan) == (0, 0)
    assert not _ada_tabel(conn, "retired_suite_snapshots")
