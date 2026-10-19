"""Demo retensi UAT (F-07) di PostgreSQL sungguhan, dengan trigger aslinya.

Membuktikan bahwa catatan uji dari scripts/demo_audit_retention.py lolos
INSERT sebagai pemilik tabel, lalu purge sebagai ilb_retention menghapus
yang 91 hari dan trigger tetap menjaga yang 89 hari. Pola koneksinya sama
dengan tests/integration/test_audit_retention_pg.py: SET LOCAL ROLE di
transaksi yang di-rollback, jadi tidak ada data yang tertinggal.
"""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.audit import service
from scripts import demo_audit_retention as demo

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
        try:
            peran = text("SELECT 1 FROM pg_roles WHERE rolname = 'ilb_retention'")
            if connection.execute(peran).first() is None:
                pytest.skip("role ilb_retention belum dibuat di database ini")
            yield connection
        finally:
            transaksi.rollback()
    engine.dispose()


def test_demo_menghapus_91_hari_dan_menyimpan_89_hari(conn):
    # Arrange
    sekarang = datetime.now(UTC)
    db = Session(bind=conn, join_transaction_mode="create_savepoint")
    baris = demo.seed_demo_rows(db, sekarang)
    sebelum = demo.existing_ids(db, [b.id for b in baris])
    conn.execute(text("SET LOCAL ROLE ilb_retention"))

    # Act
    service.purge_expired(db)
    sesudah = demo.existing_ids(db, [b.id for b in baris])

    # Assert
    assert sebelum == {b.id for b in baris}
    assert demo.outcome_ok(baris, after=sesudah, now=sekarang)
    assert "HASIL: SESUAI" in demo.render_report(baris, sebelum, sesudah, sekarang)
