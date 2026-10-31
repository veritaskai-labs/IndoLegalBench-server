"""SCRUM-205: suite per sesi dan salinan produk per sesi di PostgreSQL. PBI-11 AC2, AC6, D2.

measurement_session_suites: kasus dipilih per suite, satu sesi bisa banyak suite.
session_product_copies: data produk saat sesi dibuat, supaya perubahan produk
sesudahnya tidak mengubah hasil yang sudah berjalan. Kredensial tidak ikut disalin.

Bantuan dan alasan pemeriksaan SQLSTATE ada di tests/measurement_pg.py.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import json
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from tests.measurement_pg import (
    FOREIGN_KEY_VIOLATION,
    INVALID_TEXT_REPRESENTATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_produk,
    buat_sesi,
    sqlstate_penolakan,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

TAMBAH_SUITE = (
    "INSERT INTO measurement_session_suites (session_id, suite_id) VALUES (:sesi, :suite)"
)


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
    return buat_sesi(conn, produk_id, admin_id)


def _suite(conn, admin_id, nama: str = "Suite 205") -> uuid.UUID:
    suite_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO suites (id, name, status, created_by) "
            "VALUES (:id, :name, 'active', :admin_id)"
        ),
        {"id": suite_id, "name": f"{nama} {suite_id.hex[:8]}", "admin_id": admin_id},
    )
    return suite_id


def _salin(conn, sesi_id, produk_id, **ubah) -> uuid.UUID:
    isi = {
        "id": uuid.uuid4(),
        "session_id": sesi_id,
        "product_id": produk_id,
        "name": "DeepSeek Chat",
        "measurement_type": "automatic",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1",
        "model_name": "model-uji",
        "model_params": json.dumps({"temperature": 0.3}),
        "model_version_at_start": "model-uji-2026-10",
        "price_input_usd_per_mtok": Decimal("0.2700"),
        "price_output_usd_per_mtok": Decimal("1.1000"),
        "monthly_budget_idr": Decimal("500000.00"),
    }
    isi.update(ubah)
    return conn.execute(
        text(
            "INSERT INTO session_product_copies "
            "(id, session_id, product_id, name, measurement_type, provider_type, base_url, "
            "model_name, model_params, model_version_at_start, price_input_usd_per_mtok, "
            "price_output_usd_per_mtok, monthly_budget_idr) "
            "VALUES (:id, :session_id, :product_id, :name, "
            "CAST(:measurement_type AS ai_product_measurement_type_enum), "
            "CAST(:provider_type AS ai_product_provider_type_enum), :base_url, :model_name, "
            "CAST(:model_params AS jsonb), :model_version_at_start, :price_input_usd_per_mtok, "
            ":price_output_usd_per_mtok, :monthly_budget_idr) "
            "RETURNING id"
        ),
        isi,
    ).scalar_one()


# measurement_session_suites


def test_satu_sesi_bisa_memakai_beberapa_suite(conn, admin_id, sesi_id):
    """AC2: kasus dipilih per suite, boleh satu atau beberapa suite."""
    for suite_id in (_suite(conn, admin_id, "Ketenagakerjaan"), _suite(conn, admin_id, "Pidana")):
        conn.execute(text(TAMBAH_SUITE), {"sesi": sesi_id, "suite": suite_id})

    jumlah = conn.execute(
        text("SELECT count(*) FROM measurement_session_suites WHERE session_id = :sesi"),
        {"sesi": sesi_id},
    ).scalar_one()
    assert jumlah == 2


def test_suite_yang_sama_tidak_bisa_dua_kali_di_satu_sesi(conn, admin_id, sesi_id):
    suite_id = _suite(conn, admin_id)
    conn.execute(text(TAMBAH_SUITE), {"sesi": sesi_id, "suite": suite_id})

    sqlstate = sqlstate_penolakan(conn, TAMBAH_SUITE, {"sesi": sesi_id, "suite": suite_id})

    assert sqlstate == UNIQUE_VIOLATION


def test_suite_yang_tidak_ada_ditolak(conn, sesi_id):
    sqlstate = sqlstate_penolakan(conn, TAMBAH_SUITE, {"sesi": sesi_id, "suite": uuid.uuid4()})

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_suite_yang_dipakai_sesi_tidak_bisa_dihapus_permanen(conn, admin_id, sesi_id):
    """Corner: riwayat sesi tidak boleh kehilangan suitenya lewat CASCADE."""
    suite_id = _suite(conn, admin_id)
    conn.execute(text(TAMBAH_SUITE), {"sesi": sesi_id, "suite": suite_id})

    sqlstate = sqlstate_penolakan(conn, "DELETE FROM suites WHERE id = :id", {"id": suite_id})

    assert sqlstate == FOREIGN_KEY_VIOLATION


# session_product_copies


def test_salinan_produk_otomatis_menyimpan_parameter_dan_versi_model(conn, sesi_id, produk_id):
    salinan_id = _salin(conn, sesi_id, produk_id)

    baris = conn.execute(
        text(
            "SELECT model_params, model_version_at_start, price_output_usd_per_mtok, "
            "copied_at IS NOT NULL FROM session_product_copies WHERE id = :id"
        ),
        {"id": salinan_id},
    ).one()
    assert tuple(baris) == ({"temperature": 0.3}, "model-uji-2026-10", Decimal("1.1000"), True)


def test_salinan_produk_manual_tanpa_kolom_otomatis(conn, sesi_id, produk_id):
    """Produk manual tidak punya penyedia, model, harga, maupun anggaran."""
    salinan_id = _salin(
        conn,
        sesi_id,
        produk_id,
        measurement_type="manual",
        provider_type=None,
        base_url=None,
        model_name=None,
        model_params=None,
        model_version_at_start=None,
        price_input_usd_per_mtok=None,
        price_output_usd_per_mtok=None,
        monthly_budget_idr=None,
    )

    jenis = conn.execute(
        text("SELECT measurement_type::text FROM session_product_copies WHERE id = :id"),
        {"id": salinan_id},
    ).scalar_one()
    assert jenis == "manual"


def test_satu_sesi_hanya_punya_satu_salinan_produk(conn, sesi_id, produk_id):
    """D2: satu sesi = satu produk, jadi session_id unik."""
    _salin(conn, sesi_id, produk_id)

    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO session_product_copies (id, session_id, product_id, name, measurement_type) "
        "VALUES (:id, :sesi, :produk, 'Salinan kedua', 'manual')",
        {"id": uuid.uuid4(), "sesi": sesi_id, "produk": produk_id},
    )

    assert sqlstate == UNIQUE_VIOLATION


def test_jenis_pengukuran_salinan_hanya_nilai_enum(conn, sesi_id, produk_id):
    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO session_product_copies (id, session_id, product_id, name, measurement_type) "
        "VALUES (:id, :sesi, :produk, 'Salinan', CAST('otomatis' AS "
        "ai_product_measurement_type_enum))",
        {"id": uuid.uuid4(), "sesi": sesi_id, "produk": produk_id},
    )

    assert sqlstate == INVALID_TEXT_REPRESENTATION


def test_salinan_tidak_menyimpan_kredensial(conn):
    """D2: kredensial TIDAK ikut disalin (OWASP A02).

    Tabelnya harus ada dulu, kalau tidak "nol kolom kredensial" lulus tanpa arti.
    """
    kolom = (
        conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'session_product_copies'"
            )
        )
        .scalars()
        .all()
    )

    assert "model_params" in kolom
    assert [nama for nama in kolom if "credential" in nama or "secret" in nama] == []


def test_mengubah_produk_tidak_mengubah_salinannya(conn, sesi_id, produk_id):
    """AC6: hasil yang sudah berjalan tetap menunjuk data produk saat itu."""
    salinan_id = _salin(conn, sesi_id, produk_id, name="Nama saat sesi dibuat")

    conn.execute(
        text("UPDATE ai_products SET name = :nama WHERE id = :id"),
        {"nama": f"Nama baru {uuid.uuid4().hex[:8]}", "id": produk_id},
    )

    nama = conn.execute(
        text("SELECT name FROM session_product_copies WHERE id = :id"), {"id": salinan_id}
    ).scalar_one()
    assert nama == "Nama saat sesi dibuat"
