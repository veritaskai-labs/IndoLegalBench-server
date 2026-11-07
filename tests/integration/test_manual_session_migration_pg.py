"""SCRUM-230: migrasi sesi manual D8/D9 di PostgreSQL, termasuk rollback-nya.

D9 (ERD Jawaban Manual) menambah ke tabel D2:
- measurement_session_status_enum: not_filled, partially_filled, complete, graded,
  finalized (D8).
- measurement_sessions: answer_date_from, answer_date_to (AC13, PBI-15), finalized_at,
  finalized_by (AC8), dengan CHECK keduanya terisi bersama.
- attempts: filled_at (status D8 dihitung dari sini) dan answered_at_platform (sumber
  rentang tanggal). Kolom jawaban manual lain milik SCRUM-231.

downgrade() dijalankan langsung di koneksi tes lewat Operations.context, jadi semua DDL
ikut di-rollback fixture. Sesi tidak bisa dihapus (trigger SCRUM-205), maka data uji
tidak boleh ter-commit. Dilewati kalau TEST_POSTGRES_URL kosong.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.measurement_pg import buat_admin, buat_produk, buat_sesi, ubah_status

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

STATUS_D4 = [
    "queued",
    "running",
    "finished",
    "finished_with_failures",
    "failed",
    "paused",
    "cancelled",
]
STATUS_D8 = ["not_filled", "partially_filled", "complete", "graded", "finalized"]
KOLOM_SESI = {"answer_date_from", "answer_date_to", "finalized_at", "finalized_by"}
KOLOM_PERCOBAAN = {"filled_at", "answered_at_platform"}

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


def _migrasi():
    # Dicari lewat isi docstring, supaya tes tidak bergantung pada revision id acak.
    berkas = [p for p in VERSIONS.glob("*.py") if "SCRUM-230" in p.read_text()]
    assert len(berkas) == 1, berkas
    spec = importlib.util.spec_from_file_location("migrasi_230", berkas[0])
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _downgrade(conn) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        _migrasi().downgrade()


def _nilai_enum(conn) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'measurement_session_status_enum' ORDER BY e.enumsortorder"
            )
        ).scalars()
    )


def _kolom(conn, tabel: str, nama: set[str]) -> dict[str, str]:
    return dict(
        conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :tabel AND column_name = ANY(:nama)"
            ),
            {"tabel": tabel, "nama": list(nama)},
        ).all()
    )


def _jumlah(conn, sql: str) -> int:
    return conn.execute(text(sql)).scalar_one()


def test_head_punya_status_d8_setelah_status_d4(conn):
    """Positif: D9, lima status manual ditambahkan di belakang tujuh status D4."""
    # Assert
    assert _nilai_enum(conn) == STATUS_D4 + STATUS_D8


def test_head_punya_kolom_d9_bertipe_waktu_berzona(conn):
    """Positif: D9, rentang tanggal, tanda Selesai, dan waktu pengisian percobaan."""
    # Act
    sesi = _kolom(conn, "measurement_sessions", KOLOM_SESI)
    percobaan = _kolom(conn, "attempts", KOLOM_PERCOBAAN)

    # Assert
    assert sesi == {
        "answer_date_from": "timestamp with time zone",
        "answer_date_to": "timestamp with time zone",
        "finalized_at": "timestamp with time zone",
        "finalized_by": "uuid",
    }
    assert percobaan == dict.fromkeys(KOLOM_PERCOBAAN, "timestamp with time zone")


def test_sesi_bisa_ditandai_selesai_dengan_waktu_dan_pelakunya(conn):
    """Positif: AC8, status finalized tersimpan bersama finalized_at dan finalized_by."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)

    # Act
    ubah_status(conn, sesi_id, "finalized")
    conn.execute(
        text(
            "UPDATE measurement_sessions SET finalized_at = now(), finalized_by = :a WHERE id = :s"
        ),
        {"a": admin_id, "s": sesi_id},
    )

    # Assert
    assert _jumlah(conn, "SELECT count(*) FROM measurement_sessions WHERE finalized_by IS NOT NULL")


def test_waktu_selesai_tanpa_pelaku_ditolak(conn):
    """Negatif: D9, finalized_at tanpa finalized_by melanggar CHECK."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)

    # Act / Assert
    with pytest.raises(IntegrityError, match="finalized_complete"), conn.begin_nested():
        conn.execute(
            text("UPDATE measurement_sessions SET finalized_at = now() WHERE id = :s"),
            {"s": sesi_id},
        )


@pytest.mark.parametrize("status", STATUS_D8)
def test_downgrade_menolak_selama_ada_sesi_berstatus_d8(conn, status):
    """Negatif: rollback tidak menebak status pengganti, karena itu menulis ulang riwayat."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, status)

    # Act / Assert
    with pytest.raises(RuntimeError, match="manual"):
        _downgrade(conn)


def test_downgrade_tanpa_sesi_d8_mengembalikan_tujuh_status_dan_membuang_kolom(conn):
    """Positif: tipe dibangun ulang, default, CHECK, dan index satu-running kembali."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, "paused")

    # Act
    _downgrade(conn)

    # Assert
    assert _nilai_enum(conn) == STATUS_D4
    assert _kolom(conn, "measurement_sessions", KOLOM_SESI) == {}
    assert _kolom(conn, "attempts", KOLOM_PERCOBAAN) == {}
    assert (
        conn.execute(
            text("SELECT status::text FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
        ).scalar_one()
        == "paused"
    )
    assert _jumlah(
        conn,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'ck_measurement_sessions_running_not_archived'",
    )
    assert _jumlah(
        conn,
        "SELECT count(*) FROM pg_indexes "
        "WHERE indexname = 'uq_measurement_sessions_one_running_automatic'",
    )
    default = conn.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'measurement_sessions' AND column_name = 'status'"
        )
    ).scalar_one()
    assert default.startswith("'queued'")
