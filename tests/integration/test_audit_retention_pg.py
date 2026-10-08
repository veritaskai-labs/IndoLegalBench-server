"""Test retensi 90 hari di PostgreSQL sungguhan.

PBI-18 AC7. Hanya role ilb_retention yang boleh menghapus, dan hanya
catatan yang lebih tua dari 90 hari. Trigger tetap menolak penghapusan
lain. Dilewati kalau TEST_POSTGRES_URL kosong atau role belum dibuat.

Role diaktifkan dengan SET ROLE di dalam transaksi yang di-rollback,
jadi koneksi test cukup memakai pemilik tabel.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.audit import service

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


def _tambah(conn, umur: timedelta) -> int:
    """Owner boleh mengisi occurred_at lampau. Dipakai untuk menyiapkan data tua."""
    return conn.execute(
        text(
            "INSERT INTO audit_logs (occurred_at, action, entity_type, entity_id) "
            "VALUES (:waktu, 'suite.created', 'suite', :entity_id) RETURNING id"
        ),
        {"waktu": datetime.now(UTC) - umur, "entity_id": uuid.uuid4()},
    ).scalar_one()


def _ada(conn, row_id: int) -> bool:
    return (
        conn.execute(text("SELECT 1 FROM audit_logs WHERE id = :id"), {"id": row_id}).first()
        is not None
    )


def _hapus(conn, row_id: int) -> str | None:
    """Hapus satu baris di savepoint. None kalau berhasil, pesan error kalau ditolak."""
    savepoint = conn.begin_nested()
    try:
        conn.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": row_id})
    except DBAPIError as galat:
        savepoint.rollback()
        return str(galat.orig)
    savepoint.commit()
    return None


def test_role_retensi_boleh_menghapus_catatan_tua(conn):
    # Arrange
    tua = _tambah(conn, timedelta(days=91))
    conn.execute(text("SET LOCAL ROLE ilb_retention"))

    # Act
    galat = _hapus(conn, tua)

    # Assert
    assert galat is None
    assert not _ada(conn, tua)


def test_role_retensi_ditolak_untuk_catatan_muda(conn):
    # Arrange
    muda = _tambah(conn, timedelta(days=89))
    conn.execute(text("SET LOCAL ROLE ilb_retention"))

    # Act
    galat = _hapus(conn, muda)

    # Assert
    assert "audit_logs is append-only" in galat
    assert _ada(conn, muda)


def test_pemilik_tabel_tetap_ditolak_walau_catatan_tua(conn):
    # Arrange
    tua = _tambah(conn, timedelta(days=200))

    # Act
    galat = _hapus(conn, tua)

    # Assert
    assert "audit_logs is append-only" in galat
    assert _ada(conn, tua)


def test_role_retensi_tetap_tidak_boleh_mengubah_atau_truncate(conn):
    # Arrange
    tua = _tambah(conn, timedelta(days=91))
    conn.execute(text("SET LOCAL ROLE ilb_retention"))

    # Act + Assert
    for perintah in (f"UPDATE audit_logs SET action = 'x' WHERE id = {tua}", "TRUNCATE audit_logs"):
        savepoint = conn.begin_nested()
        with pytest.raises(DBAPIError):
            conn.execute(text(perintah))
        savepoint.rollback()
    assert _ada(conn, tua)


def test_purge_expired_hanya_menghapus_yang_lewat_90_hari(conn):
    """AC7 ujung ke ujung: service yang sama dengan job harian."""
    # Arrange
    tua = _tambah(conn, timedelta(days=120))
    muda = _tambah(conn, timedelta(days=30))
    conn.execute(text("SET LOCAL ROLE ilb_retention"))
    db = Session(bind=conn, join_transaction_mode="create_savepoint")

    # Act
    jumlah = service.purge_expired(db)

    # Assert
    assert jumlah >= 1
    assert not _ada(conn, tua)
    assert _ada(conn, muda)
