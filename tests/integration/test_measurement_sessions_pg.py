"""SCRUM-205: tabel measurement_sessions di PostgreSQL. PBI-11 AC1, AC3, D2.

Aturan kolom ditegakkan oleh database, bukan hanya oleh API, jadi dibuktikan di
PostgreSQL. Setiap tes berjalan di dalam transaksi yang di-rollback.

Tes negatif memeriksa SQLSTATE, bukan sekadar "ada error": sebelum tabel ada,
setiap INSERT juga gagal (undefined_table), dan itu tidak boleh terhitung lulus.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from collections.abc import Callable

import psycopg
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
UNIQUE_VIOLATION = "23505"
INSUFFICIENT_PRIVILEGE = "42501"


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


def _galat(conn, aksi: Callable[[], object]) -> psycopg.Error:
    """Galat database dari aksi yang harus ditolak.

    Savepoint menjaga transaksi tes tetap bisa dipakai setelah penolakan.
    """
    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as info:
        aksi()
    savepoint.rollback()
    return info.value.orig


def _ditolak(conn, **ubah) -> str:
    """SQLSTATE penolakan INSERT sesi."""
    return _galat(conn, lambda: _sesi(conn, **ubah)).sqlstate


def _sqlstate(conn, perintah: str, params: dict) -> str:
    """SQLSTATE penolakan perintah SQL apa pun."""
    return _galat(conn, lambda: conn.execute(text(perintah), params)).sqlstate


def _ubah_status(conn, sesi_id, status: str) -> None:
    conn.execute(
        text(
            "UPDATE measurement_sessions "
            "SET status = CAST(:status AS measurement_session_status_enum) WHERE id = :id"
        ),
        {"id": sesi_id, "status": status},
    )


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

    galat = _galat(
        conn,
        lambda: conn.execute(
            text(
                "UPDATE ai_products SET measurement_type = 'automatic', "
                "vendor_name = NULL, site_url = NULL WHERE id = :id"
            ),
            {"id": produk_id},
        ),
    )

    assert galat.sqlstate == CHECK_VIOLATION
    assert "MEASUREMENT_TYPE_IMMUTABLE" in str(galat)


def test_sesi_tidak_bisa_dihapus(conn, produk_id, admin_id):
    """AC10: sesi tidak dihapus, hanya diarsipkan. Berlaku juga untuk pemilik tabel."""
    sesi_id = _sesi(conn, produk_id, admin_id)

    sqlstate = _sqlstate(conn, "DELETE FROM measurement_sessions WHERE id = :id", {"id": sesi_id})

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    jumlah = conn.execute(
        text("SELECT count(*) FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert jumlah == 1


def test_tabel_sesi_tidak_bisa_dikosongkan(conn, produk_id, admin_id):
    """Corner: TRUNCATE tidak memicu trigger baris, jadi perlu trigger sendiri."""
    _sesi(conn, produk_id, admin_id)

    sqlstate = _sqlstate(conn, "TRUNCATE measurement_sessions", {})

    assert sqlstate == INSUFFICIENT_PRIVILEGE


def test_sesi_diarsipkan_dengan_pengarsipnya(conn, produk_id, admin_id):
    sesi_id = _sesi(conn, produk_id, admin_id)

    conn.execute(
        text(
            "UPDATE measurement_sessions SET archived_at = now(), archived_by = :admin_id "
            "WHERE id = :id"
        ),
        {"id": sesi_id, "admin_id": admin_id},
    )

    diarsipkan = conn.execute(
        text("SELECT archived_by FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert diarsipkan == admin_id


@pytest.mark.parametrize(
    "isian",
    ["archived_at = now()", "archived_by = :admin_id"],
    ids=["waktu-tanpa-pelaku", "pelaku-tanpa-waktu"],
)
def test_arsip_wajib_lengkap_waktu_dan_pelakunya(conn, produk_id, admin_id, isian):
    sesi_id = _sesi(conn, produk_id, admin_id)

    sqlstate = _sqlstate(
        conn,
        f"UPDATE measurement_sessions SET {isian} WHERE id = :id",
        {"id": sesi_id, "admin_id": admin_id},
    )

    assert sqlstate == CHECK_VIOLATION


def test_terima_apa_adanya_lengkap_diterima(conn, produk_id, admin_id):
    """D2: flag 'terima apa adanya' dengan waktu, pelaku, dan alasan, bukan status."""
    sesi_id = _sesi(conn, produk_id, admin_id)

    conn.execute(
        text(
            "UPDATE measurement_sessions SET accepted_as_is_at = now(), "
            "accepted_as_is_by = :admin_id, accepted_as_is_reason = 'Kegagalan dari penyedia' "
            "WHERE id = :id"
        ),
        {"id": sesi_id, "admin_id": admin_id},
    )

    status = conn.execute(
        text("SELECT status::text FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert status == "queued"


@pytest.mark.parametrize(
    "isian",
    [
        "accepted_as_is_at = now(), accepted_as_is_by = :admin_id",
        "accepted_as_is_at = now(), accepted_as_is_reason = 'Alasan'",
        "accepted_as_is_by = :admin_id, accepted_as_is_reason = 'Alasan'",
        "accepted_as_is_at = now(), accepted_as_is_by = :admin_id, accepted_as_is_reason = '   '",
    ],
    ids=["tanpa-alasan", "tanpa-pelaku", "tanpa-waktu", "alasan-hanya-spasi"],
)
def test_terima_apa_adanya_wajib_lengkap(conn, produk_id, admin_id, isian):
    sesi_id = _sesi(conn, produk_id, admin_id)

    sqlstate = _sqlstate(
        conn,
        f"UPDATE measurement_sessions SET {isian} WHERE id = :id",
        {"id": sesi_id, "admin_id": admin_id},
    )

    assert sqlstate == CHECK_VIOLATION


def test_hanya_satu_sesi_otomatis_yang_berjalan(conn, produk_id, admin_id):
    """D2: partial UNIQUE index. Sesi otomatis lain menunggu di antrean."""
    pertama = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    kedua = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    _ubah_status(conn, pertama, "running")

    sqlstate = _sqlstate(
        conn,
        "UPDATE measurement_sessions SET status = 'running' WHERE id = :id",
        {"id": kedua},
    )

    assert sqlstate == UNIQUE_VIOLATION


@pytest.mark.parametrize(
    "jenis_kedua, status_kedua, status_pertama_akhir",
    [
        ("manual", "running", "running"),
        ("automatic", "queued", "running"),
        ("automatic", "running", "finished"),
    ],
    ids=["manual-tidak-dibatasi", "otomatis-lain-menunggu", "setelah-yang-pertama-selesai"],
)
def test_sesi_yang_boleh_berdampingan_dengan_sesi_otomatis_berjalan(
    conn, produk_id, admin_id, jenis_kedua, status_kedua, status_pertama_akhir
):
    """Indeks harus parsial: tidak boleh menahan sesi yang bukan otomatis berjalan."""
    pertama = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    kedua = _sesi(conn, produk_id, admin_id, session_type=jenis_kedua)
    _ubah_status(conn, pertama, "running")
    _ubah_status(conn, pertama, status_pertama_akhir)

    _ubah_status(conn, kedua, status_kedua)

    status = conn.execute(
        text("SELECT status::text FROM measurement_sessions WHERE id = :id"), {"id": kedua}
    ).scalar_one()
    assert status == status_kedua
