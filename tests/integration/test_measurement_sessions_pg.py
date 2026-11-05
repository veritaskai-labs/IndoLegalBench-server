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
from sqlalchemy.exc import DBAPIError

from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    INVALID_TEXT_REPRESENTATION,
    STRING_DATA_RIGHT_TRUNCATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_item_snapshot,
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


# SCRUM-214, catatan Adra di #89: trim() PostgreSQL hanya membuang spasi biasa.
KOSONG_TAK_TERLIHAT = {
    "tab-baris": "\t\n",
    "nbsp": "\u00a0",
    "zwsp": "\u200b",
    "bom": "\ufeff",
    "spasi-ideografik": "\u3000",
    "campuran": " \u00a0\u200b\t",
}


@pytest.mark.parametrize("label", list(KOSONG_TAK_TERLIHAT.values()), ids=list(KOSONG_TAK_TERLIHAT))
def test_label_yang_hanya_berisi_karakter_kosong_tak_terlihat_ditolak(
    conn, produk_id, admin_id, label
):
    """Negatif: tab, NBSP, zero-width, BOM saja bukan nama sesi, walau API dilewati."""
    sqlstate = _ditolak(conn, produk_id=produk_id, admin_id=admin_id, label=label)

    assert sqlstate == CHECK_VIOLATION


def test_setiap_karakter_kosong_python_juga_ditolak_database(conn, produk_id, admin_id):
    """Edge: CHECK database dan app.shared.text memakai daftar yang sama, karakter per karakter."""
    # Di dalam tes, supaya modul yang belum ada tidak menghentikan pengumpulan file ini.
    from app.shared.text import BLANK_CHARS

    lolos = []
    for karakter in BLANK_CHARS:
        savepoint = conn.begin_nested()
        try:
            _sesi(conn, produk_id, admin_id, label=karakter * 2)
        except DBAPIError as error:
            assert error.orig.sqlstate == CHECK_VIOLATION
        else:
            lolos.append(f"U+{ord(karakter):04X}")
        savepoint.rollback()

    assert lolos == []


def test_label_dengan_karakter_tak_terlihat_di_tengah_diterima(conn, produk_id, admin_id):
    """Edge: hanya label yang seluruhnya kosong yang ditolak."""
    sesi_id = _sesi(conn, produk_id, admin_id, label="\u200bRun\u00a0November\u200b")

    label = conn.execute(
        text("SELECT label FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert label == "\u200bRun\u00a0November\u200b"


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
    """D2: flag 'terima apa adanya' dengan waktu, pelaku, dan alasan, bukan status.

    SCRUM-214: hanya sesi finished_with_failures yang boleh diterima apa adanya.
    """
    sesi_id = _sesi(conn, produk_id, admin_id)
    ubah_status(conn, sesi_id, "finished_with_failures")

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
    assert status == "finished_with_failures"


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
    # finished_with_failures: yang diuji CHECK kelengkapan, bukan trigger status SCRUM-214.
    sesi_id = _sesi(conn, produk_id, admin_id)
    ubah_status(conn, sesi_id, "finished_with_failures")

    sqlstate = sqlstate_penolakan(
        conn,
        f"UPDATE measurement_sessions SET {isian} WHERE id = :id",
        {"id": sesi_id, "admin_id": admin_id},
    )

    assert sqlstate == CHECK_VIOLATION


@pytest.mark.parametrize(
    "alasan", list(KOSONG_TAK_TERLIHAT.values()), ids=list(KOSONG_TAK_TERLIHAT)
)
def test_alasan_terima_yang_hanya_berisi_karakter_kosong_tak_terlihat_ditolak(
    conn, produk_id, admin_id, alasan
):
    """Negatif: alasan 'terima apa adanya' wajib berisi teks nyata (AC9, SCRUM-214)."""
    sesi_id = _sesi(conn, produk_id, admin_id)
    ubah_status(conn, sesi_id, "finished_with_failures")

    sqlstate = sqlstate_penolakan(
        conn,
        "UPDATE measurement_sessions SET accepted_as_is_at = now(), "
        "accepted_as_is_by = :admin_id, accepted_as_is_reason = :alasan WHERE id = :id",
        {"id": sesi_id, "admin_id": admin_id, "alasan": alasan},
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


def _arsipkan(conn, sesi_id, admin_id) -> None:
    conn.execute(
        text(
            "UPDATE measurement_sessions SET archived_at = now(), archived_by = :admin_id "
            "WHERE id = :id"
        ),
        {"id": sesi_id, "admin_id": admin_id},
    )


def test_sesi_otomatis_berjalan_tidak_bisa_diarsipkan(conn, produk_id, admin_id):
    """Review #89 blocker 1: arsip tidak mengubah status, jadi baris arsip tetap
    memegang satu-satunya slot runner, padahal SCRUM-210 menyembunyikannya."""
    sesi_id = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    ubah_status(conn, sesi_id, "running")

    sqlstate = galat(conn, lambda: _arsipkan(conn, sesi_id, admin_id)).sqlstate

    assert sqlstate == CHECK_VIOLATION


def test_sesi_arsip_tidak_bisa_mulai_berjalan(conn, produk_id, admin_id):
    """Corner: urutan sebaliknya. Sesi yang diarsipkan saat menunggu tidak boleh
    diambil runner dan menjadi baris berjalan yang tersembunyi."""
    sesi_id = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    _arsipkan(conn, sesi_id, admin_id)

    sqlstate = galat(conn, lambda: ubah_status(conn, sesi_id, "running")).sqlstate

    assert sqlstate == CHECK_VIOLATION


@pytest.mark.parametrize("status", ["finished", "finished_with_failures", "failed"])
def test_sesi_selesai_tetap_bisa_diarsipkan(conn, produk_id, admin_id, status):
    """Guard: aturan baru hanya menahan status running, bukan arsip AC10 lainnya.
    Sesi queued sudah dibuktikan test_sesi_diarsipkan_dengan_pengarsipnya."""
    sesi_id = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    ubah_status(conn, sesi_id, "running")
    ubah_status(conn, sesi_id, status)

    _arsipkan(conn, sesi_id, admin_id)

    diarsipkan = conn.execute(
        text("SELECT archived_by FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert diarsipkan == admin_id


def test_jenis_sesi_berjalan_tidak_bisa_diubah(conn, produk_id, admin_id):
    """Review #89 blocker 1: mengubah sesi otomatis berjalan menjadi manual
    mengeluarkannya dari indeks, lalu dua sesi otomatis bisa berjalan bersamaan."""
    pertama = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    ubah_status(conn, pertama, "running")

    sqlstate = sqlstate_penolakan(
        conn,
        "UPDATE measurement_sessions SET session_type = 'manual' WHERE id = :id",
        {"id": pertama},
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    kedua = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    assert galat(conn, lambda: ubah_status(conn, kedua, "running")).sqlstate == UNIQUE_VIOLATION


def test_jenis_sesi_yang_masih_menunggu_bisa_diubah(conn, produk_id, admin_id):
    """Guard: sebelum berjalan, sesi belum mengukur apa pun, jadi jenisnya masih boleh
    dikoreksi."""
    sesi_id = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)

    conn.execute(
        text("UPDATE measurement_sessions SET session_type = 'manual' WHERE id = :id"),
        {"id": sesi_id},
    )

    jenis = conn.execute(
        text("SELECT session_type::text FROM measurement_sessions WHERE id = :id"),
        {"id": sesi_id},
    ).scalar_one()
    assert jenis == "manual"


_IDENTITAS_BARU = {
    "purpose": "CAST('trial' AS measurement_purpose_enum)",
    "split_filter": "CAST('test' AS measurement_split_filter_enum)",
    "repetitions": "1",
    "session_type": "CAST('manual' AS measurement_session_type_enum)",
    "product_id": ":produk_lain",
    "judge_provider_id": "gen_random_uuid()",
}


def _ubah_identitas(conn, sesi_id, kolom, admin_id) -> None:
    conn.execute(
        text(f"UPDATE measurement_sessions SET {kolom} = {_IDENTITAS_BARU[kolom]} WHERE id = :id"),
        {"id": sesi_id, "produk_lain": buat_produk(conn, admin_id)},
    )


@pytest.mark.parametrize("kolom", list(_IDENTITAS_BARU))
def test_identitas_sesi_selesai_tidak_bisa_diubah(conn, produk_id, admin_id, kolom):
    """Review #89 blocker 2: hasil harus tetap terikat pada apa yang diukur. Memindah
    product_id juga melepas kunci PBI-23 produk asal dan meninggalkan salinannya."""
    sesi_id = _sesi(
        conn, produk_id, admin_id, session_type="automatic", repetitions=3, split_filter="dev"
    )
    ubah_status(conn, sesi_id, "running")
    ubah_status(conn, sesi_id, "finished")

    sqlstate = galat(conn, lambda: _ubah_identitas(conn, sesi_id, kolom, admin_id)).sqlstate

    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize("kolom", list(_IDENTITAS_BARU))
def test_identitas_sesi_yang_masih_menunggu_bisa_diubah(conn, produk_id, admin_id, kolom):
    """Guard: sebelum berjalan, sesi belum mengukur apa pun, jadi masih boleh dikoreksi."""
    sesi_id = _sesi(
        conn, produk_id, admin_id, session_type="automatic", repetitions=3, split_filter="dev"
    )

    _ubah_identitas(conn, sesi_id, kolom, admin_id)

    status = conn.execute(
        text("SELECT status::text FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert status == "queued"


def _percobaan_ketiga(conn, sesi_id, admin_id) -> None:
    conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :sesi, :item, 3, 'automatic')"
        ),
        {"id": uuid.uuid4(), "sesi": sesi_id, "item": buat_item_snapshot(conn, sesi_id, admin_id)},
    )


def test_pengulangan_tidak_bisa_diturunkan_di_bawah_percobaan_yang_ada(conn, produk_id, admin_id):
    """Review #89 blocker 2, corner: batas attempts hanya diperiksa saat percobaan
    ditulis. Menurunkan pengulangan sesudahnya meninggalkan percobaan nomor 3 pada
    sesi 1 pengulangan, bahkan selagi sesi masih queued."""
    sesi_id = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=3)
    _percobaan_ketiga(conn, sesi_id, admin_id)

    sqlstate = sqlstate_penolakan(
        conn, "UPDATE measurement_sessions SET repetitions = 2 WHERE id = :id", {"id": sesi_id}
    )

    assert sqlstate == CHECK_VIOLATION


def test_pengulangan_boleh_turun_sampai_nomor_percobaan_tertinggi(conn, produk_id, admin_id):
    """Guard batas: turun tepat ke nomor percobaan tertinggi masih sah."""
    sesi_id = _sesi(conn, produk_id, admin_id, session_type="automatic", repetitions=5)
    _percobaan_ketiga(conn, sesi_id, admin_id)

    conn.execute(
        text("UPDATE measurement_sessions SET repetitions = 3 WHERE id = :id"), {"id": sesi_id}
    )

    pengulangan = conn.execute(
        text("SELECT repetitions FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()
    assert pengulangan == 3


def test_sesi_baru_belum_punya_waktu_antrean(conn, produk_id, admin_id):
    """D2: queued_at boleh kosong, diisi saat sesi masuk antrean (SCRUM-207)."""
    sesi_id = _sesi(conn, produk_id, admin_id)

    antre = conn.execute(
        text("SELECT queued_at FROM measurement_sessions WHERE id = :id"), {"id": sesi_id}
    ).scalar_one()

    assert antre is None


def test_sesi_baru_boleh_belum_punya_penilai(conn, produk_id, admin_id):
    """AC1: penilai LLM dipilih saat membuat sesi, tetapi tabel penilai milik PBI-14
    belum ada, jadi kolomnya boleh kosong dan belum ber-FK (review #89)."""
    sesi_id = _sesi(conn, produk_id, admin_id)

    penilai = conn.execute(
        text("SELECT judge_provider_id FROM measurement_sessions WHERE id = :id"),
        {"id": sesi_id},
    ).scalar_one()

    assert penilai is None


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
