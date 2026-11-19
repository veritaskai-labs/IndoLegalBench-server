"""SCRUM-300: migrasi b6f3a2d81c47 bisa dibalik di PostgreSQL 16 (Jira: "Migrasi punya rollback").

Pola sama dengan test_grading_progress_pg.py: berkas migrasi dimuat lewat path lalu
downgrade dan upgrade dijalankan di dalam satu transaksi yang di-rollback (DDL PostgreSQL
transaksional), jadi database tes tetap di head setelahnya. CI menjalankan juga
upgrade head → downgrade base → upgrade head lewat alembic.

Dilewati kalau TEST_POSTGRES_URL kosong. Label: Positif, Edge.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
ROOT = Path(__file__).resolve().parents[2]
BERKAS = ROOT / "migrations" / "versions" / "b6f3a2d81c47_report_tables.py"

TABEL = ("reports", "report_sessions", "report_results", "report_case_basis", "published_reports")
ENUM = (
    "report_score_scope_enum",
    "report_score_split_enum",
    "report_measurement_filter_enum",
    "report_measurement_kind_enum",
    "report_label_mode_enum",
    "report_language_enum",
    "report_status_enum",
)
FUNGSI = (
    "reports_guard_update",
    "report_rows_forbid_change",
    "report_rows_check_insert",
    "published_reports_check_insert",
    "published_reports_check_commit",
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


def _jalankan(conn, langkah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_tabel_laporan", BERKAS)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, langkah)()


def _ada(conn, sql: str, nama: str) -> bool:
    return conn.execute(text(sql), {"nama": nama}).scalar_one()


def _tabel(conn, nama):
    return _ada(conn, "SELECT to_regclass(:nama) IS NOT NULL", nama)


def _enum(conn, nama):
    return _ada(conn, "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :nama)", nama)


def _fungsi(conn, nama):
    return _ada(conn, "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = :nama)", nama)


def _trigger(conn) -> set[str]:
    return set(
        conn.execute(
            text(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                "AND tgrelid::regclass::text = ANY(:tabel)"
            ),
            {"tabel": list(TABEL)},
        ).scalars()
    )


def test_head_memuat_tabel_enum_fungsi_dan_trigger(conn):
    """Positif: setelah upgrade semua objek D1 ada, termasuk trigger beku."""
    assert all(_tabel(conn, t) for t in TABEL)
    assert all(_enum(conn, e) for e in ENUM)
    assert all(_fungsi(conn, f) for f in FUNGSI)
    assert {
        "reports_guard_update",
        "reports_block_delete",
        "reports_block_truncate",
        "report_results_block_change",
        "report_results_check_insert",
        "published_reports_check_insert",
        "published_reports_check_commit",
    } <= _trigger(conn)


def test_downgrade_menghapus_semuanya_lalu_upgrade_memulihkan(conn):
    """Positif: rollback bersih (tabel, enum, fungsi), dan upgrade ulang berhasil."""
    _jalankan(conn, "downgrade")

    sisa = (
        [t for t in TABEL if _tabel(conn, t)]
        + [e for e in ENUM if _enum(conn, e)]
        + [f for f in FUNGSI if _fungsi(conn, f)]
    )
    assert sisa == []

    _jalankan(conn, "upgrade")

    assert all(_tabel(conn, t) for t in TABEL)
    assert all(_fungsi(conn, f) for f in FUNGSI)


def test_downgrade_tidak_menyentuh_enum_split_tag_kasus(conn):
    """Edge: case_split_tag_enum dipakai ulang dari migrasi kasus, jadi tidak ikut dihapus."""
    _jalankan(conn, "downgrade")

    assert _enum(conn, "case_split_tag_enum")
