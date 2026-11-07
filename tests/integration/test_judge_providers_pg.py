"""SCRUM-258: tabel judge_providers dan FK penilai dari sesi dan grading run (D6).

Penilai LLM dipilih saat membuat pengukuran (klien No. 61) dan punya anggaran
sendiri yang terpisah dari produk (No. 63 A). Tabelnya terpisah dari ai_products
supaya penilai tidak muncul di pilihan produk, dashboard, dan laporan (catatan SA
6 Nov). Constraint diuji langsung di PostgreSQL karena SQLite tidak menegakkan FK.

Isolasi: transaksi per tes di-rollback, dan setiap perintah yang harus ditolak
dibungkus savepoint (tests/measurement_pg.galat). Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from tests.grading_pg import buat_run, buat_versi_aktif
from tests.judge_pg import buat_penilai
from tests.measurement_pg import (
    CHECK_VIOLATION,
    FOREIGN_KEY_VIOLATION,
    INVALID_TEXT_REPRESENTATION,
    STRING_DATA_RIGHT_TRUNCATION,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_produk,
    buat_sesi,
    galat,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
NOT_NULL_VIOLATION = "23502"

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


def _baris(conn, penilai_id: uuid.UUID):
    return (
        conn.execute(
            text(
                "SELECT provider_type::text AS provider_type, is_active, created_at, updated_at "
                "FROM judge_providers WHERE id = :id"
            ),
            {"id": penilai_id},
        )
        .mappings()
        .one()
    )


def _sqlstate_buat(conn, admin_id, **ubah) -> str:
    return galat(conn, lambda: buat_penilai(conn, admin_id, **ubah)).sqlstate


# --- Baris sah --------------------------------------------------------------------


@pytest.mark.parametrize(
    "jenis", ["openai_compatible", "gemini_interactions", "anthropic_messages"]
)
def test_penilai_sah_tersimpan_dengan_jenis_provider_yang_sudah_ada(conn, admin_id, jenis):
    """Positif: enum ProviderType dipakai ulang, ketiga adapter bisa jadi penilai."""
    penilai = buat_penilai(conn, admin_id, provider_type=jenis)

    assert _baris(conn, penilai)["provider_type"] == jenis


def test_penilai_baru_aktif_dan_bercap_waktu(conn, admin_id):
    """Positif: is_active, created_at, updated_at punya default di database."""
    penilai_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO judge_providers (id, name, provider_type, base_url, model_name, "
            "credential_encrypted, credential_hint, price_input_usd_per_mtok, "
            "price_output_usd_per_mtok, monthly_budget_idr, created_by) VALUES (:id, 'Penilai A', "
            "'openai_compatible', 'https://penilai.contoh.test', 'model-a', :kunci, 'abcd', 0, 0, "
            "1, :admin)"
        ),
        {"id": penilai_id, "kunci": b"ciphertext-sintetis", "admin": admin_id},
    )

    baris = _baris(conn, penilai_id)

    assert baris["is_active"] is True
    assert baris["created_at"] is not None
    assert baris["updated_at"] is not None


def test_harga_nol_diterima(conn, admin_id):
    """Edge: model penilai gratis tetap sah; biaya per baris jadi 0, bukan tidak diketahui."""
    penilai = buat_penilai(
        conn,
        admin_id,
        price_input_usd_per_mtok=Decimal("0"),
        price_output_usd_per_mtok=Decimal("0"),
    )

    assert penilai is not None


# --- Kolom wajib dan CHECK --------------------------------------------------------


@pytest.mark.parametrize(
    "kolom",
    [
        "name",
        "provider_type",
        "base_url",
        "model_name",
        "credential_encrypted",
        "credential_hint",
        "price_input_usd_per_mtok",
        "price_output_usd_per_mtok",
        "monthly_budget_idr",
        "created_by",
    ],
)
def test_kolom_wajib_tidak_boleh_kosong(conn, admin_id, kolom):
    """Negatif: penilai tanpa kredensial, harga, atau anggaran tidak bisa dipakai menilai."""
    assert _sqlstate_buat(conn, admin_id, **{kolom: None}) == NOT_NULL_VIOLATION


@pytest.mark.parametrize("kolom", ["name", "base_url", "model_name"])
@pytest.mark.parametrize("isi", ["", "   ", "​ "], ids=["kosong", "spasi", "zero-width"])
def test_teks_wajib_tidak_boleh_hanya_karakter_kosong(conn, admin_id, kolom, isi):
    """Negatif: sama dengan aturan sql_not_blank di tabel lain (SCRUM-214)."""
    assert _sqlstate_buat(conn, admin_id, **{kolom: isi}) == CHECK_VIOLATION


@pytest.mark.parametrize(
    ("hint", "sqlstate"),
    [("abc", CHECK_VIOLATION), ("", CHECK_VIOLATION), ("abcde", STRING_DATA_RIGHT_TRUNCATION)],
    ids=["tiga", "kosong", "lima"],
)
def test_hint_kredensial_selalu_empat_karakter(conn, admin_id, hint, sqlstate):
    """Negatif: hint = empat karakter terakhir (crypto.HINT_LENGTH). Lebih pendek ditolak
    CHECK; lebih panjang sudah ditolak varchar(4) sebelum CHECK dijalankan."""
    assert _sqlstate_buat(conn, admin_id, credential_hint=hint) == sqlstate


@pytest.mark.parametrize("kolom", ["price_input_usd_per_mtok", "price_output_usd_per_mtok"])
def test_harga_negatif_ditolak(conn, admin_id, kolom):
    assert _sqlstate_buat(conn, admin_id, **{kolom: Decimal("-0.0001")}) == CHECK_VIOLATION


@pytest.mark.parametrize("anggaran", [Decimal("0"), Decimal("-1")], ids=["nol", "negatif"])
def test_anggaran_bulanan_harus_positif(conn, admin_id, anggaran):
    """Negatif: No. 63 A, anggaran penilai terpisah; nol berarti penilai tidak bisa dipakai."""
    assert _sqlstate_buat(conn, admin_id, monthly_budget_idr=anggaran) == CHECK_VIOLATION


def test_jenis_provider_tidak_dikenal_ditolak(conn, admin_id):
    assert _sqlstate_buat(conn, admin_id, provider_type="ollama") == INVALID_TEXT_REPRESENTATION


def test_nama_penilai_unik(conn, admin_id):
    """Negatif: dropdown SCRUM-211 memilih berdasarkan nama, jadi nama tidak boleh kembar."""
    buat_penilai(conn, admin_id, name="DeepSeek penilai")

    assert _sqlstate_buat(conn, admin_id, name="DeepSeek penilai") == UNIQUE_VIOLATION


def test_nama_penilai_nonaktif_tetap_terpakai(conn, admin_id):
    """Corner: penilai lama yang dinonaktifkan masih dirujuk sesi lama, namanya tidak dipakai ulang."""
    buat_penilai(conn, admin_id, name="Penilai lama", is_active=False)

    assert _sqlstate_buat(conn, admin_id, name="Penilai lama") == UNIQUE_VIOLATION


def test_pembuat_harus_pengguna_yang_ada(conn, admin_id):
    assert _sqlstate_buat(conn, admin_id, created_by=uuid.uuid4()) == FOREIGN_KEY_VIOLATION


# --- FK dari sesi pengukuran ------------------------------------------------------


def test_sesi_merujuk_penilai_yang_ada(conn, admin_id):
    """Positif: AC6, penilai yang dipilih di form tersimpan di sesi."""
    penilai = buat_penilai(conn, admin_id)

    sesi = buat_sesi(conn, buat_produk(conn, admin_id), admin_id, judge_provider_id=penilai)

    tersimpan = conn.execute(
        text("SELECT judge_provider_id FROM measurement_sessions WHERE id = :id"), {"id": sesi}
    ).scalar_one()
    assert tersimpan == penilai


def test_sesi_dengan_penilai_tidak_dikenal_ditolak_fk(conn, admin_id):
    """Negatif: dulu uuid apa pun diterima (schemas.py "belum divalidasi")."""
    produk = buat_produk(conn, admin_id)

    sqlstate = galat(
        conn,
        lambda: buat_sesi(conn, produk, admin_id, judge_provider_id=uuid.uuid4()),
    ).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_penilai_yang_dirujuk_sesi_tidak_bisa_dihapus(conn, admin_id):
    """Negatif: ON DELETE RESTRICT, sesi lama tidak boleh kehilangan penilainya."""
    penilai = buat_penilai(conn, admin_id)
    buat_sesi(conn, buat_produk(conn, admin_id), admin_id, judge_provider_id=penilai)

    sqlstate = galat(
        conn,
        lambda: conn.execute(text("DELETE FROM judge_providers WHERE id = :id"), {"id": penilai}),
    ).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


# --- FK dari grading run ----------------------------------------------------------


def test_run_merujuk_penilai_yang_ada(conn, admin_id):
    penilai = buat_penilai(conn, admin_id)
    sesi = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)

    run = buat_run(
        conn, sesi, buat_versi_aktif(conn, admin_id), admin_id, judge_provider_id=penilai
    )

    tersimpan = conn.execute(
        text("SELECT judge_provider_id FROM grading_runs WHERE id = :id"), {"id": run}
    ).scalar_one()
    assert tersimpan == penilai


def test_run_dengan_penilai_tidak_dikenal_ditolak_fk(conn, admin_id):
    """Negatif: #111 sengaja belum memberi FK sampai tiket ini."""
    sesi = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    versi = buat_versi_aktif(conn, admin_id)

    sqlstate = galat(
        conn,
        lambda: buat_run(conn, sesi, versi, admin_id, judge_provider_id=uuid.uuid4()),
    ).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_penilai_yang_dirujuk_run_tidak_bisa_dihapus(conn, admin_id):
    penilai = buat_penilai(conn, admin_id)
    sesi = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    buat_run(conn, sesi, buat_versi_aktif(conn, admin_id), admin_id, judge_provider_id=penilai)

    sqlstate = galat(
        conn,
        lambda: conn.execute(text("DELETE FROM judge_providers WHERE id = :id"), {"id": penilai}),
    ).sqlstate

    assert sqlstate == FOREIGN_KEY_VIOLATION
