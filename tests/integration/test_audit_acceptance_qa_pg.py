"""Uji penerimaan retensi audit di PostgreSQL. Sub task QA SCRUM-142.

PBI-18 AC7 dengan data berumur 89, tepat 90, dan 91 hari dalam satu kali
jalan job, memakai jam yang sama untuk data dan job. Hanya catatan yang
lebih tua dari 90 hari yang hilang. AC2 (pemilik tabel ditolak berapa pun
umur catatannya) sudah diuji di test_audit_immutable_pg.py dan
test_audit_retention_pg.py, jadi tidak diulang di sini.

Dilewati kalau TEST_POSTGRES_URL kosong. CI mengisinya setelah migration.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.audit import service

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

SEKARANG = datetime.now(UTC).replace(microsecond=0)


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


def _catatan_berumur(conn, hari: int) -> int:
    return conn.execute(
        text(
            "INSERT INTO audit_logs (occurred_at, action, entity_type, entity_id) "
            "VALUES (:waktu, 'suite.created', 'suite', :entity_id) RETURNING id"
        ),
        {"waktu": SEKARANG - timedelta(days=hari), "entity_id": uuid.uuid4()},
    ).scalar_one()


def _ada(conn, row_id: int) -> bool:
    baris = conn.execute(text("SELECT 1 FROM audit_logs WHERE id = :id"), {"id": row_id})
    return baris.first() is not None


def test_ac7_positif_job_hanya_menghapus_yang_lewat_90_hari(conn):
    umur = {hari: _catatan_berumur(conn, hari) for hari in (89, 90, 91)}
    conn.execute(text("SET LOCAL ROLE ilb_retention"))
    db = Session(bind=conn, join_transaction_mode="create_savepoint")

    service.purge_expired(db, now=SEKARANG)

    assert {hari: _ada(conn, row_id) for hari, row_id in umur.items()} == {
        89: True,
        90: True,
        91: False,
    }
