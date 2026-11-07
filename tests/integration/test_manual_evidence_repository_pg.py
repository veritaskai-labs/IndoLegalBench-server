"""EvidenceRepository dan DbEvidenceStorage di PostgreSQL sungguhan. PBI-22 (SCRUM-176) AC7; SCRUM-233.

Yang hanya terbukti di PostgreSQL:
- session_status(lock=True) memakai FOR SHARE pada kolom terpilih (SQLite mengabaikannya);
- trigger attempt_evidence_guard tetap menolak bukti keempat walau service dilewati;
- isi berkas BYTEA kembali utuh dan ikut terhapus bersama baris buktinya.

Session ORM diikat ke koneksi tes dengan savepoint, jadi commit di repository hanya
menutup savepoint dan semuanya dibatalkan di akhir tes. Dilewati kalau TEST_POSTGRES_URL
kosong.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.runs.models import EvidenceKind, SessionStatus
from app.modules.runs.session_ports import NewEvidence
from tests.measurement_pg import (
    CHECK_VIOLATION,
    buat_admin,
    buat_item_snapshot,
    buat_produk,
    buat_sesi,
    ubah_status,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

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
def db(conn):
    with Session(bind=conn, join_transaction_mode="create_savepoint") as sesi:
        yield sesi


@pytest.fixture
def repo():
    # Diimpor lewat fixture: modul yang belum ada membuat tes merah, bukan galat koleksi.
    from app.modules.runs import manual_evidence_repository

    return manual_evidence_repository


@pytest.fixture
def jawaban(conn):
    admin = buat_admin(conn)
    sesi = buat_sesi(conn, buat_produk(conn, admin), admin, session_type="manual")
    ubah_status(conn, sesi, "partially_filled")
    item = buat_item_snapshot(conn, sesi, admin)
    attempt = conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :sesi, :item, 1, 'manual') RETURNING id"
        ),
        {"id": uuid.uuid4(), "sesi": sesi, "item": item},
    ).scalar_one()
    return sesi, item, attempt, admin


def _tautan(attempt, admin):
    return NewEvidence(
        attempt_id=attempt, kind=EvidenceKind.LINK, uploaded_by=admin, url="https://x.test"
    )


def test_status_sesi_dikunci_dan_percobaan_ditemukan(db, repo, jawaban):
    """Positif: FOR SHARE pada kolom sesi sah di PostgreSQL; percobaan manual ditemukan."""
    # Arrange
    sesi, item, attempt, _ = jawaban
    penyimpan = repo.EvidenceRepository(db)

    # Act
    status = penyimpan.session_status(sesi, lock=True)
    sasaran = penyimpan.find_attempt(sesi, item, 1)

    # Assert
    assert status is SessionStatus.PARTIALLY_FILLED
    assert sasaran.attempt_id == attempt


def test_trigger_menolak_bukti_keempat_walau_service_dilewati(db, repo, jawaban):
    """Negatif: AC7, D9 batas 3 dijaga database, bukan hanya service."""
    # Arrange
    _, _, attempt, admin = jawaban
    penyimpan = repo.EvidenceRepository(db)
    for _ in range(3):
        penyimpan.add(_tautan(attempt, admin))

    # Act
    with pytest.raises(DBAPIError) as galat:
        penyimpan.add(_tautan(attempt, admin))

    # Assert
    assert galat.value.orig.sqlstate == CHECK_VIOLATION


def test_berkas_tersimpan_terbaca_dan_terhapus_bersama_buktinya(db, repo, jawaban):
    """Positif: AC7, BYTEA utuh; hapus bukti menghapus berkasnya di transaksi yang sama."""
    # Arrange
    sesi, _, attempt, admin = jawaban
    penyimpan, berkas = repo.EvidenceRepository(db), repo.DbEvidenceStorage(db)
    isi = b"%PDF-1.7\n" + bytes(range(256)) * 4
    key = berkas.put(isi)
    bukti = penyimpan.add(
        NewEvidence(
            attempt_id=attempt,
            kind=EvidenceKind.FILE,
            uploaded_by=admin,
            file_key=key,
            file_name="percakapan.pdf",
            mime_type="application/pdf",
            size_bytes=len(isi),
        )
    )

    # Act
    terbaca = berkas.read(key)
    ditemukan = penyimpan.find_evidence(sesi, bukti.id)
    penyimpan.remove(bukti.id)
    berkas.delete(key)

    # Assert
    assert terbaca == isi
    assert ditemukan[0].file_key == key
    assert (berkas.read(key), penyimpan.find_evidence(sesi, bukti.id)) == (None, None)


def test_bukti_tidak_terlihat_dari_sesi_lain(db, repo, jawaban, conn):
    """Negatif: id bukti hanya berlaku di sesinya sendiri."""
    # Arrange
    _, _, attempt, admin = jawaban
    penyimpan = repo.EvidenceRepository(db)
    bukti = penyimpan.add(_tautan(attempt, admin))
    sesi_lain = buat_sesi(conn, buat_produk(conn, admin), admin, session_type="manual")

    # Act / Assert
    assert penyimpan.find_evidence(sesi_lain, bukti.id) is None
