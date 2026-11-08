"""SCRUM-271: migrasi yang memindahkan FK penilai dari judge_providers ke ai_products.

Sebelum migrasi, measurement_sessions.judge_provider_id dan grading_runs.judge_provider_id
menunjuk judge_providers. Nilai lama bukan id produk, jadi dikosongkan, lalu FK dipasang ke
ai_products. Downgrade melakukan hal yang sama ke arah sebaliknya. Trigger penjaga sesi dan
grading run dimatikan hanya di dalam transaksi migrasi; tes memastikan hidup lagi sesudahnya.

upgrade() dan downgrade() dijalankan langsung di koneksi tes lewat Operations.context,
supaya DDL dan data ikut di-rollback (sesi dan run tidak bisa dihapus). Dilewati kalau
TEST_POSTGRES_URL kosong.

Label: Positif = FK dan nilai benar, Negatif = rujukan tidak sah ditolak, Edge = trigger.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.grading_pg import buat_run, buat_versi_aktif
from tests.judge_pg import buat_penilai, buat_produk_penilai
from tests.measurement_pg import FOREIGN_KEY_VIOLATION, buat_admin, buat_produk, buat_sesi, galat

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
ROOT = Path(__file__).resolve().parents[2]
BERKAS = ROOT / "migrations" / "versions" / "a7c3e91b4d52_judge_from_ai_products.py"
FK_SESI = "fk_measurement_sessions_judge_provider"
FK_RUN = "fk_grading_runs_judge_provider"

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
def admin_id(conn) -> uuid.UUID:
    return buat_admin(conn)


def _jalankan(conn, langkah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_judge_dari_produk", BERKAS)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, langkah)()


def _tabel_tujuan(conn, nama_fk: str) -> str:
    return conn.execute(
        text("SELECT confrelid::regclass::text FROM pg_constraint WHERE conname = :nama"),
        {"nama": nama_fk},
    ).scalar_one()


def _penjaga_hidup(conn, trigger: str) -> bool:
    status = conn.execute(
        text("SELECT tgenabled FROM pg_trigger WHERE tgname = :nama"), {"nama": trigger}
    ).scalar_one()
    return status == "O"


def _ke_keadaan_lama(conn, admin_id):
    """Database seperti sebelum migrasi: FK ke judge_providers, sesi dan run menunjuk penilai lama."""
    _jalankan(conn, "downgrade")
    penilai = buat_penilai(conn, admin_id)
    sesi = buat_sesi(conn, buat_produk(conn, admin_id), admin_id, judge_provider_id=penilai)
    run = buat_run(
        conn, sesi, buat_versi_aktif(conn, admin_id), admin_id, judge_provider_id=penilai
    )
    return sesi, run


def _nilai(conn, tabel: str, baris) -> uuid.UUID | None:
    return conn.execute(
        text(f"SELECT judge_provider_id FROM {tabel} WHERE id = :id"), {"id": baris}
    ).scalar_one()


def test_upgrade_memindahkan_kedua_fk_ke_ai_products(conn, admin_id):
    """Positif: setelah upgrade kedua FK menunjuk ai_products."""
    # Arrange
    _ke_keadaan_lama(conn, admin_id)
    assert _tabel_tujuan(conn, FK_SESI) == "judge_providers"

    # Act
    _jalankan(conn, "upgrade")

    # Assert
    assert _tabel_tujuan(conn, FK_SESI) == "ai_products"
    assert _tabel_tujuan(conn, FK_RUN) == "ai_products"


def test_upgrade_mengosongkan_rujukan_ke_penilai_lama(conn, admin_id):
    """Positif: nilai lama bukan id produk, jadi dikosongkan; tidak ada yang menggantung."""
    # Arrange
    sesi, run = _ke_keadaan_lama(conn, admin_id)
    assert _nilai(conn, "measurement_sessions", sesi) is not None

    # Act
    _jalankan(conn, "upgrade")

    # Assert
    assert _nilai(conn, "measurement_sessions", sesi) is None
    assert _nilai(conn, "grading_runs", run) is None


def test_setelah_upgrade_sesi_menerima_produk_dan_menolak_id_acak(conn, admin_id):
    """Negatif: FK baru menerima produk otomatis dan menolak id yang tidak ada di ai_products."""
    # Arrange
    _ke_keadaan_lama(conn, admin_id)
    _jalankan(conn, "upgrade")
    produk_penilai = buat_produk_penilai(conn, admin_id)
    produk = buat_produk(conn, admin_id)

    # Act
    sesi = buat_sesi(conn, produk, admin_id, judge_provider_id=produk_penilai)
    sqlstate = galat(
        conn, lambda: buat_sesi(conn, produk, admin_id, judge_provider_id=uuid.uuid4())
    ).sqlstate

    # Assert
    assert _nilai(conn, "measurement_sessions", sesi) == produk_penilai
    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_penjaga_sesi_dan_run_hidup_lagi_setelah_upgrade_dan_downgrade(conn, admin_id):
    """Edge: trigger dimatikan hanya selama UPDATE di dalam migrasi."""
    # Arrange
    _ke_keadaan_lama(conn, admin_id)

    # Act
    _jalankan(conn, "upgrade")
    sesudah_upgrade = (
        _penjaga_hidup(conn, "measurement_sessions_guard_update"),
        _penjaga_hidup(conn, "grading_runs_guard"),
    )
    _jalankan(conn, "downgrade")
    sesudah_downgrade = (
        _penjaga_hidup(conn, "measurement_sessions_guard_update"),
        _penjaga_hidup(conn, "grading_runs_guard"),
    )

    # Assert
    assert sesudah_upgrade == (True, True)
    assert sesudah_downgrade == (True, True)


def test_downgrade_mengosongkan_rujukan_ke_produk_dan_mengembalikan_fk(conn, admin_id):
    """Positif: nilai yang menunjuk produk bukan id judge_providers, jadi dikosongkan."""
    # Arrange
    produk_penilai = buat_produk_penilai(conn, admin_id)
    sesi = buat_sesi(conn, buat_produk(conn, admin_id), admin_id, judge_provider_id=produk_penilai)

    # Act
    _jalankan(conn, "downgrade")

    # Assert
    assert _nilai(conn, "measurement_sessions", sesi) is None
    assert _tabel_tujuan(conn, FK_SESI) == "judge_providers"
    assert _tabel_tujuan(conn, FK_RUN) == "judge_providers"
