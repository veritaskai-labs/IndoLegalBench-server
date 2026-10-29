"""SCRUM-183: user_role_enum di PostgreSQL yang sudah dimigrasi menerima developer_ai.

SQLite tidak menjalankan DDL ENUM, jadi hanya test ini yang membuktikan
migrasi ad76e1ba6c12 benar-benar menambah nilainya. Baris dibuat di dalam
transaksi yang dibatalkan di akhir test.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


def test_enum_peran_memuat_semua_nilai_role():
    from app.shared.security import Role

    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        nilai = koneksi.execute(
            text("SELECT unnest(enum_range(NULL::user_role_enum))::text")
        ).scalars()
        assert set(nilai) == {role.value for role in Role}
    engine.dispose()


def test_pengguna_developer_ai_bisa_disimpan():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            koneksi.execute(
                text(
                    "INSERT INTO users (id, email, name, role, is_active, created_at, updated_at) "
                    "VALUES (:id, :email, 'Dev AI', 'developer_ai', true, now(), now())"
                ),
                {"id": uuid.uuid4(), "email": f"dev-ai-{uuid.uuid4().hex[:8]}@veritask.test"},
            )
            peran = koneksi.execute(
                text("SELECT role::text FROM users WHERE name = 'Dev AI'")
            ).scalar_one()
            assert peran == "developer_ai"
        finally:
            transaksi.rollback()
    engine.dispose()
