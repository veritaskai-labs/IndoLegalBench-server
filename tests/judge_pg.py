"""Bantuan tes PostgreSQL untuk tabel judge_providers (PBI-14, SCRUM-258).

Kredensial di sini bukan ciphertext Fernet sungguhan: tabel hanya menyimpan bytea,
enkripsi diuji di service. Semua nilai sintetis (L3, L5).

Fungsi menerima koneksi yang transaksinya di-rollback oleh fixture tes.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Connection, text

KOLOM_PENILAI = (
    "id",
    "name",
    "provider_type",
    "base_url",
    "model_name",
    "credential_encrypted",
    "credential_hint",
    "price_input_usd_per_mtok",
    "price_output_usd_per_mtok",
    "monthly_budget_idr",
    "is_active",
    "created_by",
)


def isi_penilai(admin_id: uuid.UUID, **ubah) -> dict:
    """Satu baris penilai yang sah; ubah menimpa kolom yang diuji."""
    penilai_id = uuid.uuid4()
    return {
        "id": penilai_id,
        "name": f"Penilai uji {penilai_id.hex[:8]}",
        "provider_type": "openai_compatible",
        "base_url": "https://penilai.contoh.test/v1/chat/completions",
        "model_name": "model-penilai-sintetis",
        "credential_encrypted": b"ciphertext-sintetis",
        "credential_hint": "abcd",
        "price_input_usd_per_mtok": Decimal("0.2700"),
        "price_output_usd_per_mtok": Decimal("1.1000"),
        "monthly_budget_idr": Decimal("500000.00"),
        "is_active": True,
        "created_by": admin_id,
    } | ubah


def buat_penilai(conn: Connection, admin_id: uuid.UUID, **ubah) -> uuid.UUID:
    nilai = isi_penilai(admin_id, **ubah)
    kolom = ", ".join(KOLOM_PENILAI)
    params = ", ".join(
        "CAST(:provider_type AS ai_product_provider_type_enum)"
        if nama == "provider_type"
        else f":{nama}"
        for nama in KOLOM_PENILAI
    )
    conn.execute(text(f"INSERT INTO judge_providers ({kolom}) VALUES ({params})"), nilai)
    return nilai["id"]


def buat_produk_penilai(conn: Connection, admin_id: uuid.UUID, **ubah) -> uuid.UUID:
    """Penilai sejak SCRUM-271: produk AI otomatis. FK sesi dan grading run menunjuk ke sini."""
    produk_id = uuid.uuid4()
    nilai = {
        "id": produk_id,
        "name": f"Produk penilai {produk_id.hex[:8]}",
        "admin": admin_id,
    } | ubah
    conn.execute(
        text(
            "INSERT INTO ai_products (id, name, measurement_type, provider_type, base_url, "
            "model_name, credential_encrypted, credential_hint, rate_limit_per_minute, "
            "monthly_budget_idr, created_by) "
            "VALUES (:id, :name, 'automatic', 'openai_compatible', "
            "'https://penilai.contoh.test/v1/chat/completions', 'model-penilai-sintetis', "
            "'\\x63697068657274657874', 'abcd', 30, 500000, :admin)"
        ),
        nilai,
    )
    return produk_id
