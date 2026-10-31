"""SCRUM-205: tabel measurement_sessions di PostgreSQL. PBI-11 AC1, AC3, D2.

Aturan kolom ditegakkan oleh database, bukan hanya oleh API, jadi dibuktikan di
PostgreSQL. Setiap tes berjalan di dalam transaksi yang di-rollback.
Bantuan dan alasan pemeriksaan SQLSTATE ada di tests/measurement_pg.py.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text

from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    INVALID_TEXT_REPRESENTATION,
    STRING_DATA_RIGHT_TRUNCATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_produk,
    galat,
    sqlstate_penolakan,
    ubah_status,
)
from tests.measurement_pg import buat_sesi as _sesi

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
def admin_id(conn) -> uuid.UUID:
    return buat_admin(conn)


@pytest.fixture
def produk_id(conn, admin_id) -> uuid.UUID:
    return buat_produk(conn, admin_id)


def _ditolak(conn, **ubah) -> str:
    """SQLSTATE penolakan INSERT sesi."""
    return galat(conn, lambda: _sesi(conn, **ubah)).sqlstate


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

    penolakan = galat(
        conn,
        lambda: conn.execute(
            text(
                "UPDATE ai_products SET measurement_type = 'automatic', "
                "vendor_name = NULL, site_url = NULL WHERE id = :id"
            ),
            {"id": produk_id},
        ),
    )

    assert penolakan.sqlstate == CHECK_VIOLATION
    assert "MEASUREMENT_TYPE_IMMUTABLE" in str(penolakan)


def test_sesi_tidak_bisa_dihapus(conn, produk_id, admin_id):
    """AC10: sesi tidak dihapus, hanya diarsipkan. Berlaku juga untuk pemilik tabel."""
    sesi_id = _sesi(conn, produk_id, admin_id)

    sqlstate = sqlstate_penolakan(
        conn, "DELETE FROM measurement_sessions WHERE id = :id", {"id": sesi_id}
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    jumlah = conn.execute(
        text("SELECT count(*) FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert jumlah == 1


def test_tabel_sesi_tidak_bisa_dikosongkan(conn, produk_id, admin_id):
    """Corner: TRUNCATE tidak memicu trigger baris, jadi perlu trigger sendiri.

    CASCADE: setelah tabel lain menunjuk sesi, TRUNCATE biasa sudah ditolak
    PostgreSQL karena FK (0A000). CASCADE melewati FK itu dan akan ikut
    mengosongkan suite dan salinan produk, jadi itu yang harus ditahan trigger.
    """
    _sesi(conn, produk_id, admin_id)

    sqlstate = sqlstate_penolakan(conn, "TRUNCATE measurement_sessions CASCADE", {})

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

    sqlstate = sqlstate_penolakan(
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

    sqlstate = sqlstate_penolakan(
        conn,
        f"UPDATE measurement_sessions SET {isian} WHERE id = :id",
        {"id": sesi_id, "admin_id": admin_id},
    )

    assert sqlstate == CHECK_VIOLATION


def test_hanya_satu_sesi_otomatis_yang_berjalan(conn, produk_id, admin_id):
    """D2: partial UNIQUE index. Sesi otomatis lain menunggu di antrean."""
    pertama = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    kedua = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    ubah_status(conn, pertama, "running")

    sqlstate = sqlstate_penolakan(
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
    ubah_status(conn, pertama, "running")
    ubah_status(conn, pertama, status_pertama_akhir)

    ubah_status(conn, kedua, status_kedua)

    status = conn.execute(
        text("SELECT status::text FROM measurement_sessions WHERE id = :id"), {"id": kedua}
    ).scalar_one()
    assert status == status_kedua


def test_sesi_baru_belum_punya_waktu_antrean(conn, produk_id, admin_id):
    """D2: queued_at boleh kosong, diisi saat sesi masuk antrean (SCRUM-207)."""
    sesi_id = _sesi(conn, produk_id, admin_id)

    antre = conn.execute(
        text("SELECT queued_at FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()

    assert antre is None


def test_waktu_antrean_menyimpan_zona_waktu(conn, produk_id, admin_id):
    """Corner: 08.00 WIB dan 01.00 UTC adalah saat yang sama. Tanpa zona waktu,
    urutan FIFO antara waktu WIB dan UTC bisa tertukar."""
    sesi_id = _sesi(conn, produk_id, admin_id)
    wib = datetime(2026, 11, 2, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta"))

    conn.execute(
        text("UPDATE measurement_sessions SET queued_at = :antre WHERE id = :id"),
        {"antre": wib, "id": sesi_id},
    )

    antre = conn.execute(
        text("SELECT queued_at FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert antre == datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
