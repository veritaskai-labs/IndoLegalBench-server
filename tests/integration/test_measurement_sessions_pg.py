"""SCRUM-205: tabel measurement_sessions di PostgreSQL. PBI-11 AC1, AC3, D2.

Aturan kolom ditegakkan oleh database, bukan hanya oleh API, jadi dibuktikan di
PostgreSQL. Setiap tes berjalan di dalam transaksi yang di-rollback.

Tes negatif memeriksa SQLSTATE, bukan sekadar "ada error": sebelum tabel ada,
setiap INSERT juga gagal (undefined_table), dan itu tidak boleh terhitung lulus.
Dilewati kalau TEST_POSTGRES_URL kosong.
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

CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"
INVALID_TEXT_REPRESENTATION = "22P02"
STRING_DATA_RIGHT_TRUNCATION = "22001"


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
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, name, role) VALUES (:id, :email, 'Admin 205', 'admin')"
        ),
        {"id": user_id, "email": f"admin-205-{user_id.hex}@veritask.test"},
    )
    return user_id


@pytest.fixture
def produk_id(conn, admin_id) -> uuid.UUID:
    """Produk manual: kolom wajibnya paling sedikit, cukup untuk FK sesi."""
    product_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ai_products (id, name, created_by, measurement_type, vendor_name, site_url) "
            "VALUES (:id, :name, :admin_id, 'manual', 'Penyedia Uji', 'https://contoh.test')"
        ),
        {"id": product_id, "name": f"Produk 205 {product_id.hex[:8]}", "admin_id": admin_id},
    )
    return product_id


def _sesi(conn, produk_id, admin_id, **ubah) -> uuid.UUID:
    isi = {
        "id": uuid.uuid4(),
        "label": "Uji coba DeepSeek Oktober",
        "purpose": "official",
        "session_type": "manual",
        "product_id": produk_id,
        "repetitions": 1,
        "split_filter": "dev",
        "created_by": admin_id,
    }
    isi.update(ubah)
    return conn.execute(
        text(
            "INSERT INTO measurement_sessions "
            "(id, label, purpose, session_type, product_id, repetitions, split_filter, created_by) "
            "VALUES (:id, :label, CAST(:purpose AS measurement_purpose_enum), "
            "CAST(:session_type AS measurement_session_type_enum), :product_id, :repetitions, "
            "CAST(:split_filter AS measurement_split_filter_enum), :created_by) "
            "RETURNING id"
        ),
        isi,
    ).scalar_one()


def _ditolak(conn, **ubah) -> str:
    """SQLSTATE penolakan. Savepoint menjaga transaksi tes tetap bisa dipakai."""
    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as info:
        _sesi(conn, **ubah)
    savepoint.rollback()
    return info.value.orig.sqlstate


def test_sesi_baru_menunggu_dan_tercatat_waktunya(conn, produk_id, admin_id):
    # Arrange, Act
    sesi_id = _sesi(conn, produk_id, admin_id)

    # Assert
    baris = conn.execute(
        text(
            "SELECT status::text, created_at IS NOT NULL, updated_at IS NOT NULL, "
            "started_at, finished_at, archived_at "
            "FROM measurement_sessions WHERE id = :id"
        ),
        {"id": sesi_id},
    ).one()
    assert tuple(baris) == ("queued", True, True, None, None, None)


@pytest.mark.parametrize("pengulangan", [1, 3, 5])
def test_pengulangan_satu_sampai_lima_diterima(conn, produk_id, admin_id, pengulangan):
    """Batas bawah, nilai bawaan otomatis, dan batas atas AC3."""
    sesi_id = _sesi(conn, produk_id=produk_id, admin_id=admin_id, repetitions=pengulangan)

    assert sesi_id is not None


@pytest.mark.parametrize("pengulangan", [0, 6, -1])
def test_pengulangan_di_luar_satu_sampai_lima_ditolak(conn, produk_id, admin_id, pengulangan):
    """Tepat di luar batas (0, 6) dan nilai negatif."""
    sqlstate = _ditolak(conn, produk_id=produk_id, admin_id=admin_id, repetitions=pengulangan)

    assert sqlstate == CHECK_VIOLATION


@pytest.mark.parametrize("label", ["", "   "])
def test_label_kosong_atau_hanya_spasi_ditolak(conn, produk_id, admin_id, label):
    """AC1: sesi wajib diberi nama. Spasi saja tidak dihitung sebagai nama."""
    sqlstate = _ditolak(conn, produk_id=produk_id, admin_id=admin_id, label=label)

    assert sqlstate == CHECK_VIOLATION


def test_label_dua_ratus_karakter_diterima_lebih_ditolak(conn, produk_id, admin_id):
    _sesi(conn, produk_id, admin_id, label="a" * 200)

    sqlstate = _ditolak(conn, produk_id=produk_id, admin_id=admin_id, label="a" * 201)

    assert sqlstate == STRING_DATA_RIGHT_TRUNCATION


@pytest.mark.parametrize(
    "kolom, nilai",
    [
        ("purpose", "resmi"),
        ("session_type", "otomatis"),
        ("split_filter", "semua"),
    ],
)
def test_nilai_enum_di_luar_daftar_ditolak(conn, produk_id, admin_id, kolom, nilai):
    sqlstate = _ditolak(conn, produk_id=produk_id, admin_id=admin_id, **{kolom: nilai})

    assert sqlstate == INVALID_TEXT_REPRESENTATION


def test_produk_yang_tidak_ada_ditolak(conn, produk_id, admin_id):
    sqlstate = _ditolak(conn, produk_id=uuid.uuid4(), admin_id=admin_id)

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_jenis_produk_terkunci_setelah_ada_sesi(conn, produk_id, admin_id):
    """Kontrak dengan trigger PBI-23 (a1f8c7e3d092): riwayat dibaca dari tabel ini.

    Trigger itu memakai ERRCODE check_violation, sama dengan CHECK C1 produk, jadi
    yang membedakan keduanya adalah pesannya.
    """
    _sesi(conn, produk_id, admin_id)

    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as info:
        conn.execute(
            text(
                "UPDATE ai_products SET measurement_type = 'automatic', "
                "vendor_name = NULL, site_url = NULL WHERE id = :id"
            ),
            {"id": produk_id},
        )
    savepoint.rollback()

    assert info.value.orig.sqlstate == CHECK_VIOLATION
    assert "MEASUREMENT_TYPE_IMMUTABLE" in str(info.value.orig)
