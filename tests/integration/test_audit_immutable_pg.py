"""Test AC2 di PostgreSQL sungguhan, terhadap database hasil alembic upgrade head.

PBI-18 AC2, diagram D6c: "tes AC2 wajib berjalan di PostgreSQL (bukan
SQLite) dan membuktikan UPDATE/DELETE ditolak". Dilewati kalau
TEST_POSTGRES_URL kosong. CI mengisinya setelah langkah migration.

Setiap test berjalan di dalam transaksi yang di-rollback, jadi database
tidak berubah setelah test selesai.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as connection:
        transaksi = connection.begin()
        connection.execute(
            text(
                "INSERT INTO audit_logs (action, entity_type, entity_id) "
                "VALUES ('suite.created', 'suite', :entity_id)"
            ),
            {"entity_id": uuid.uuid4()},
        )
        try:
            yield connection
        finally:
            transaksi.rollback()
    engine.dispose()


def _ditolak(conn, perintah: str) -> str:
    """Jalankan perintah di savepoint, kembalikan pesan error PostgreSQL."""
    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as galat:
        conn.execute(text(perintah))
    savepoint.rollback()
    return str(galat.value.orig)


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE audit_logs SET action = 'suite.updated'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ],
)
def test_pemilik_tabel_pun_ditolak_trigger(conn, perintah):
    """Koneksi ini memakai pemilik tabel. Hak akses tidak menahannya, trigger yang menahan."""
    # Act
    pesan = _ditolak(conn, perintah)

    # Assert
    assert "audit_logs is append-only" in pesan
    assert conn.execute(text("SELECT count(*) FROM audit_logs")).scalar_one() >= 1


def test_trigger_terpasang_sesuai_d6c(conn):
    # Act
    nama = set(
        conn.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal"
            )
        ).scalars()
    )

    # Assert
    assert nama == {"audit_logs_block_mutation", "audit_logs_block_truncate"}


def test_role_aplikasi_hanya_boleh_baca_dan_tambah(conn):
    """Lapisan hak akses D6c. Hanya berlaku kalau role ilb_app memang dibuat."""
    # Arrange
    ada = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'ilb_app'")).first()
    if ada is None:
        pytest.skip("role ilb_app belum dibuat di database ini")

    # Act
    hak = {
        privilege: conn.execute(
            text("SELECT has_table_privilege('ilb_app', 'audit_logs', :privilege)"),
            {"privilege": privilege},
        ).scalar_one()
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
    }

    # Assert
    assert hak == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
    }
