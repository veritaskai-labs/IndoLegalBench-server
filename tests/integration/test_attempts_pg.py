"""SCRUM-205: percobaan per kasus per pengulangan di PostgreSQL. PBI-11 AC3, AC8, D2.

attempts: satu baris per sesi, item snapshot, dan nomor pengulangan. Jawaban mentah
hanya ditulis sekali dan tidak pernah diubah atau dihapus, supaya hasil sesi yang
berbeda waktu tetap bisa dibandingkan. Status tetap bisa bergerak.

Bantuan dan alasan pemeriksaan SQLSTATE ada di tests/measurement_pg.py.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.measurement_pg import (
    CHECK_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    INVALID_TEXT_REPRESENTATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_item_snapshot,
    buat_produk,
    buat_sesi,
    galat,
    sqlstate_penolakan,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

JAWABAN = "**Berhak.** Pasal 156 UU 13/2003 mengatur pesangon."
UBAH_JAWABAN = "UPDATE attempts SET answer_text = :jawaban WHERE id = :id"


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


@pytest.fixture
def produk_id(conn, admin_id) -> uuid.UUID:
    return buat_produk(conn, admin_id)


@pytest.fixture
def sesi_id(conn, produk_id, admin_id) -> uuid.UUID:
    """Sesi otomatis dengan 3 pengulangan, nilai bawaan AC3."""
    return buat_sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)


@pytest.fixture
def item_id(conn, sesi_id, admin_id) -> uuid.UUID:
    return buat_item_snapshot(conn, sesi_id, admin_id)


def _percobaan(conn, sesi_id, item_id, **ubah) -> uuid.UUID:
    isi = {
        "id": uuid.uuid4(),
        "session_id": sesi_id,
        "snapshot_item_id": item_id,
        "repetition_no": 1,
        "source": "automatic",
    }
    isi.update(ubah)
    return conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :session_id, :snapshot_item_id, :repetition_no, "
            "CAST(:source AS attempt_source_enum)) RETURNING id"
        ),
        isi,
    ).scalar_one()


def _ditolak(conn, **ubah) -> str:
    return galat(conn, lambda: _percobaan(conn, **ubah)).sqlstate


def _jawab(conn, percobaan_id, jawaban: str = JAWABAN) -> None:
    conn.execute(
        text("UPDATE attempts SET answer_text = :jawaban, status = 'succeeded' WHERE id = :id"),
        {"jawaban": jawaban, "id": percobaan_id},
    )


def _baris(conn, percobaan_id) -> tuple:
    return tuple(
        conn.execute(
            text("SELECT status::text, answer_text FROM attempts WHERE id = :id"),
            {"id": percobaan_id},
        ).one()
    )


# Bentuk dan keunikan


def test_percobaan_baru_menunggu_tanpa_jawaban(conn, sesi_id, item_id):
    percobaan_id = _percobaan(conn, sesi_id, item_id)

    assert _baris(conn, percobaan_id) == ("pending", None)


def test_nomor_pengulangan_yang_sama_tidak_bisa_dua_kali(conn, sesi_id, item_id):
    """D2: coba ulang menambah call log, bukan baris percobaan baru."""
    _percobaan(conn, sesi_id, item_id, repetition_no=2)

    sqlstate = _ditolak(conn, sesi_id=sesi_id, item_id=item_id, repetition_no=2)

    assert sqlstate == UNIQUE_VIOLATION


@pytest.mark.parametrize("nomor", [1, 3])
def test_nomor_pengulangan_sampai_jumlah_pengulangan_sesi_diterima(conn, sesi_id, item_id, nomor):
    assert _percobaan(conn, sesi_id, item_id, repetition_no=nomor) is not None


@pytest.mark.parametrize("nomor", [0, 4])
def test_nomor_pengulangan_di_luar_jumlah_pengulangan_sesi_ditolak(conn, sesi_id, item_id, nomor):
    """Corner: 4 masih di dalam batas 1-5, tapi sesi ini hanya 3 pengulangan."""
    sqlstate = _ditolak(conn, sesi_id=sesi_id, item_id=item_id, repetition_no=nomor)

    assert sqlstate == CHECK_VIOLATION


@pytest.mark.parametrize(
    "kolom, nilai",
    [("source", "otomatis"), ("status", "selesai")],
)
def test_nilai_enum_di_luar_daftar_ditolak(conn, sesi_id, item_id, kolom, nilai):
    percobaan_id = _percobaan(conn, sesi_id, item_id)
    tipe = "attempt_source_enum" if kolom == "source" else "attempt_status_enum"

    sqlstate = sqlstate_penolakan(
        conn,
        f"UPDATE attempts SET {kolom} = CAST(:nilai AS {tipe}) WHERE id = :id",
        {"nilai": nilai, "id": percobaan_id},
    )

    assert sqlstate == INVALID_TEXT_REPRESENTATION


# Jawaban mentah permanen (AC8)


def test_status_bergerak_lalu_jawaban_ditulis_sekali(conn, sesi_id, item_id):
    percobaan_id = _percobaan(conn, sesi_id, item_id)
    conn.execute(
        text("UPDATE attempts SET status = 'running' WHERE id = :id"), {"id": percobaan_id}
    )

    _jawab(conn, percobaan_id)

    assert _baris(conn, percobaan_id) == ("succeeded", JAWABAN)


def test_kegagalan_teknis_tanpa_jawaban(conn, sesi_id, item_id):
    percobaan_id = _percobaan(conn, sesi_id, item_id)

    conn.execute(
        text("UPDATE attempts SET status = 'technical_failure' WHERE id = :id"),
        {"id": percobaan_id},
    )

    assert _baris(conn, percobaan_id) == ("technical_failure", None)


@pytest.mark.parametrize(
    "jawaban_baru",
    ["Jawaban lain", None, ""],
    ids=["diganti", "dikosongkan-null", "dikosongkan-string"],
)
def test_jawaban_yang_sudah_ada_tidak_bisa_diubah(conn, sesi_id, item_id, jawaban_baru):
    percobaan_id = _percobaan(conn, sesi_id, item_id)
    _jawab(conn, percobaan_id)

    sqlstate = sqlstate_penolakan(conn, UBAH_JAWABAN, {"jawaban": jawaban_baru, "id": percobaan_id})

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    assert _baris(conn, percobaan_id) == ("succeeded", JAWABAN)


def test_jawaban_yang_sama_ditulis_ulang_tetap_diterima(conn, sesi_id, item_id):
    """Corner: menulis nilai yang sama bukan perubahan, worker boleh mengulang simpan."""
    percobaan_id = _percobaan(conn, sesi_id, item_id)
    _jawab(conn, percobaan_id)

    conn.execute(text(UBAH_JAWABAN), {"jawaban": JAWABAN, "id": percobaan_id})

    assert _baris(conn, percobaan_id) == ("succeeded", JAWABAN)


@pytest.mark.parametrize(
    "perintah",
    ["DELETE FROM attempts WHERE id = :id", "TRUNCATE attempts"],
    ids=["delete", "truncate"],
)
def test_percobaan_tidak_bisa_dihapus(conn, sesi_id, item_id, perintah):
    percobaan_id = _percobaan(conn, sesi_id, item_id)
    _jawab(conn, percobaan_id)

    sqlstate = sqlstate_penolakan(conn, perintah, {"id": percobaan_id})

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    assert _baris(conn, percobaan_id) == ("succeeded", JAWABAN)
