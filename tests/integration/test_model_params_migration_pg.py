"""SCRUM-177 SCRUM-247: migrasi parameter lama di tabel ai_products PostgreSQL.

Yang tidak bisa dibuktikan SQLite: kolom JSONB dibaca dan ditulis ulang, produk
Manual (model_params NULL, constraint C1) tidak tersentuh, dan produk terhapus
ikut diubah. Fungsi migrasi dipanggil langsung di dalam transaksi yang
dibatalkan di akhir test, seperti test_legal_refs_migration_pg.py.
"""

import importlib.util
import json
import logging
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "b7e2c9d41a58_model_params_provider_native_json.py"
)


def _muat_migrasi():
    spec = importlib.util.spec_from_file_location("migrasi_model_params_pg", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            # Produk lain di database bersama ikut diubah fungsi migrasi; dikosongkan
            # di dalam transaksi supaya hitungan dan log hanya berisi produk test ini.
            koneksi.execute(text("UPDATE ai_products SET model_params = NULL"))
            yield koneksi
        finally:
            transaksi.rollback()
    engine.dispose()


def _admin(conn) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text("INSERT INTO users (id, email, name, role) VALUES (:id, :email, 'QA 247', 'admin')"),
        {"id": user_id, "email": f"qa-247-{user_id}@veritask.test"},
    )
    return user_id


def _otomatis(conn, admin, penyedia: str, params: dict | None, *, terhapus=False) -> uuid.UUID:
    product_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ai_products (id, name, provider_type, base_url, model_name, "
            "credential_encrypted, credential_hint, rate_limit_per_minute, monthly_budget_idr, "
            "model_params, is_active, created_by, created_at, updated_at, deleted_at) "
            "VALUES (:id, :nama, CAST(:penyedia AS ai_product_provider_type_enum), "
            "'https://api.example.test/v1', 'model-247', 'cipher', 'hint', 20, 750000, "
            "CAST(:params AS jsonb), true, :admin, now(), now(), "
            "CASE WHEN :hapus THEN now() END)"
        ),
        {
            "id": product_id,
            "nama": f"QA247-{product_id.hex[:8]}",
            "penyedia": penyedia,
            "params": None if params is None else json.dumps(params),
            "admin": admin,
            "hapus": terhapus,
        },
    )
    return product_id


def _manual(conn, admin) -> uuid.UUID:
    product_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ai_products (id, name, measurement_type, vendor_name, site_url, "
            "manual_params, is_active, created_by, created_at, updated_at) VALUES (:id, :nama, "
            "'manual', 'Penyedia', 'https://contoh.test', 'temperature 0.3', true, :admin, "
            "now(), now())"
        ),
        {"id": product_id, "nama": f"QA247-M-{product_id.hex[:8]}", "admin": admin},
    )
    return product_id


def _params(conn, product_id: uuid.UUID):
    return conn.execute(
        text("SELECT model_params FROM ai_products WHERE id = :id"), {"id": product_id}
    ).scalar_one()


def test_upgrade_mengubah_parameter_lama_dan_melaporkan_yang_dibuang(conn, caplog):
    """Positif: body yang dikirim sama dengan sebelum SCRUM-247; kunci yang dibuang tercatat."""
    migrasi = _muat_migrasi()
    admin = _admin(conn)
    openai = _otomatis(conn, admin, "openai_compatible", {"temperature": 0.3, "effort": "high"})
    gemini = _otomatis(
        conn, admin, "gemini_interactions", {"temperature": 0.3, "thinking": True}, terhapus=True
    )
    kosong = _otomatis(conn, admin, "anthropic_messages", {"web_search": True})
    manual = _manual(conn, admin)

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        migrasi.ubah_ke_json_penyedia(conn)

    assert _params(conn, openai) == {"temperature": 0.3, "reasoning_effort": "high"}
    assert _params(conn, gemini) == {"generation_config": {"temperature": 0.3}}
    assert _params(conn, kosong) is None
    assert _params(conn, manual) is None
    pesan = caplog.text
    assert str(gemini) in pesan and "thinking" in pesan
    assert str(kosong) in pesan and "web_search" in pesan
    assert str(openai) not in pesan


def test_downgrade_mengembalikan_bentuk_lama(conn, caplog):
    """Positif: setelah rollback, skema lama (extra=forbid) bisa membaca semua baris."""
    migrasi = _muat_migrasi()
    admin = _admin(conn)
    openai = _otomatis(conn, admin, "openai_compatible", {"reasoning_effort": "low", "top_p": 0.9})
    gemini = _otomatis(
        conn, admin, "gemini_interactions", {"generation_config": {"temperature": 1}}
    )

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        migrasi.kembalikan_ke_bentuk_lama(conn)

    assert _params(conn, openai) == {"effort": "low"}
    assert _params(conn, gemini) == {"temperature": 1}
    assert str(openai) in caplog.text and "top_p" in caplog.text
