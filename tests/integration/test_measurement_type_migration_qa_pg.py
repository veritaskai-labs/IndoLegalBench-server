"""Migrasi jenis pengukuran a1f8c7e3d092 di PostgreSQL. PBI-23 AC4, AC5, SCRUM-204.

SCRUM-200 (#79) belum punya tes migrasi. Yang dibuktikan di sini:
- AC5: produk yang terdaftar sebelum fitur ini menjadi Otomatis dan seluruh datanya utuh
  (kredensial terenkripsi, batas, hasil uji koneksi, status, soft delete);
- AC4 nama unik selamanya: nama ganda lama (Sprint 2 mengizinkan nama produk terhapus
  dipakai ulang) diselesaikan sebelum constraint uq_ai_products_name dipasang;
- rollback (DoD Sprint 3 "migrasi bisa di-rollback").

Dua hal dipotret apa adanya karena berbeda dengan teks Jira SCRUM-200/204, dan dicatat
sebagai deviasi di PR (SCRUM-204):
- nama ganda tidak "dilaporkan": baris terhapus diganti namanya diam-diam menjadi
  "<nama> (dihapus <8 karakter id>)", tanpa log; nama lama tidak kembali saat downgrade;
- downgrade menghapus permanen produk Manual, karena skema lama tidak mengenalnya.

Database diturunkan ke revisi sebelum a1f8c7e3d092 lalu dinaikkan lagi, dan selalu
dikembalikan ke head di akhir. Downgrade menghapus produk Manual milik tes lain, jadi
jalankan hanya di database tes sementara. Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.shared.config import get_settings

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
SEBELUM = "b19c5e7d2a41"  # pragma: allowlist secret (revision id)
ROOT = Path(__file__).resolve().parents[2]
# Kolom ai_products sebelum PBI-23, yang semuanya harus utuh setelah migrasi (AC5).
KOLOM_LAMA = (
    "name, provider_type::text, base_url, model_name, credential_encrypted, credential_hint, "
    "rate_limit_per_minute, monthly_budget_idr, is_active, last_test_at, "
    "last_test_status::text, last_test_message, last_test_error_category::text, "
    "created_by, created_at, deleted_at"
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def alembic_cfg(monkeypatch):
    """Alembic membaca URL dari settings (migrations/env.py), jadi arahkan ke database tes."""
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    yield cfg
    command.upgrade(cfg, "head")
    get_settings.cache_clear()


@pytest.fixture
def engine():
    mesin = create_engine(POSTGRES_URL)
    yield mesin
    mesin.dispose()


def _admin(conn) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, name, role, is_active, created_at, updated_at) "
            "VALUES (:id, :email, 'QA 204', 'admin', true, now(), now())"
        ),
        {"id": user_id, "email": f"m204-{user_id.hex}@veritask.test"},
    )
    return user_id


def _produk_lama(conn, admin, nama: str, *, terhapus: bool = False, diuji: bool = False):
    """Satu baris ai_products di skema sebelum PBI-23 (semua isian Otomatis wajib)."""
    product_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ai_products (id, name, provider_type, base_url, model_name, "
            "credential_encrypted, credential_hint, rate_limit_per_minute, monthly_budget_idr, "
            "is_active, last_test_at, last_test_status, last_test_message, "
            "last_test_error_category, created_by, created_at, updated_at, deleted_at) "
            "VALUES (:id, :nama, 'openai_compatible', 'https://api.example.test/v1', "
            "'model-lama', :cipher, 'lama', 20, 750000.50, :aktif, :uji_at, :uji_status, "
            ":uji_pesan, :uji_kategori, :admin, now(), now(), :hapus)"
        ),
        {
            "id": product_id,
            "nama": nama,
            "cipher": f"ciphertext-{product_id.hex}".encode(),
            "aktif": not terhapus,
            "uji_at": datetime.now(UTC) if diuji else None,
            "uji_status": "failed" if diuji else None,
            "uji_pesan": "model tidak ditemukan" if diuji else None,
            "uji_kategori": "model_not_found" if diuji else None,
            "admin": admin,
            "hapus": datetime.now(UTC) if terhapus else None,
        },
    )
    return product_id


def _baris(conn, product_id) -> tuple:
    return tuple(
        conn.execute(
            text(f"SELECT {KOLOM_LAMA} FROM ai_products WHERE id = :id"), {"id": product_id}
        ).one()
    )


def _nama(conn, product_id) -> str | None:
    return conn.scalar(text("SELECT name FROM ai_products WHERE id = :id"), {"id": product_id})


def test_produk_lama_menjadi_otomatis_dengan_data_utuh(alembic_cfg, engine):
    """Positif (AC5): produk aktif, nonaktif terhapus, dan yang pernah diuji koneksi menjadi
    Otomatis; setiap kolom lama, termasuk ciphertext kredensial, sama persis."""
    # Arrange
    command.downgrade(alembic_cfg, SEBELUM)
    akhiran = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        admin = _admin(conn)
        ids = [
            _produk_lama(conn, admin, f"QA204 Lama A {akhiran}"),
            _produk_lama(conn, admin, f"QA204 Lama B {akhiran}", diuji=True),
            _produk_lama(conn, admin, f"QA204 Lama C {akhiran}", terhapus=True),
        ]
    with engine.connect() as conn:
        sebelum = [_baris(conn, product_id) for product_id in ids]

    # Act
    command.upgrade(alembic_cfg, "head")

    # Assert
    with engine.connect() as conn:
        assert [_baris(conn, product_id) for product_id in ids] == sebelum
        jenis = conn.scalars(
            text("SELECT measurement_type::text FROM ai_products WHERE id = ANY(:ids)"),
            {"ids": ids},
        ).all()
        kosong = conn.scalar(
            text(
                "SELECT count(*) FROM ai_products WHERE id = ANY(:ids) AND (vendor_name IS NOT "
                "NULL OR site_url IS NOT NULL OR access_note IS NOT NULL OR manual_params IS "
                "NOT NULL OR model_params IS NOT NULL)"
            ),
            {"ids": ids},
        )
    assert (jenis, kosong) == (["automatic"] * 3, 0)


def test_nama_ganda_lama_diganti_pada_produk_terhapus_saja(alembic_cfg, engine):
    """Edge (AC4): nama yang dipakai ulang setelah produk dihapus. Produk aktif tetap
    bernama sama; setiap produk terhapus yang namanya ganda diganti dengan akhiran id-nya,
    lalu constraint unik selamanya terpasang. Nama terhapus yang tidak ganda tidak diubah.

    Deviasi dari Jira SCRUM-200 ("melaporkan sebelum constraint dipasang"): penggantian ini
    tidak dilaporkan di mana pun."""
    # Arrange
    command.downgrade(alembic_cfg, SEBELUM)
    akhiran = uuid.uuid4().hex[:8]
    ganda, sendiri = f"QA204 Ganda {akhiran}", f"QA204 Sendiri {akhiran}"
    with engine.begin() as conn:
        admin = _admin(conn)
        terhapus_1 = _produk_lama(conn, admin, ganda, terhapus=True)
        terhapus_2 = _produk_lama(conn, admin, ganda, terhapus=True)
        aktif = _produk_lama(conn, admin, ganda)
        tunggal = _produk_lama(conn, admin, sendiri, terhapus=True)

    # Act
    command.upgrade(alembic_cfg, "head")

    # Assert
    with engine.connect() as conn:
        assert _nama(conn, aktif) == ganda
        assert _nama(conn, tunggal) == sendiri
        for terhapus in (terhapus_1, terhapus_2):
            assert _nama(conn, terhapus) == f"{ganda} (dihapus {str(terhapus)[:8]})"
        constraint = conn.scalar(
            text(
                "SELECT contype FROM pg_constraint WHERE conname = 'uq_ai_products_name' "
                "AND conrelid = 'ai_products'::regclass"
            )
        )
    assert constraint == "u"


def test_nama_panjang_tetap_muat_setelah_diganti(alembic_cfg, engine):
    """Edge: nama 200 karakter dipotong ke 181 sebelum diberi akhiran, jadi tetap muat
    di varchar(200) dan migrasi tidak gagal."""
    # Arrange
    command.downgrade(alembic_cfg, SEBELUM)
    panjang = f"QA204 {uuid.uuid4().hex}".ljust(200, "x")
    with engine.begin() as conn:
        admin = _admin(conn)
        terhapus = _produk_lama(conn, admin, panjang, terhapus=True)
        _produk_lama(conn, admin, panjang)

    # Act
    command.upgrade(alembic_cfg, "head")

    # Assert
    with engine.connect() as conn:
        baru = _nama(conn, terhapus)
    assert baru == f"{panjang[:181]} (dihapus {str(terhapus)[:8]})"
    assert len(baru) == 200


def test_rollback_menghapus_produk_manual_dan_tidak_mengembalikan_nama(alembic_cfg, engine):
    """Edge (DoD rollback): downgrade berjalan dan skema lama kembali, tetapi produk Manual
    dihapus permanen (skema lama mewajibkan kredensial) dan nama produk terhapus yang
    sudah diganti tidak dikembalikan. Dipotret apa adanya sebagai deviasi."""
    # Arrange
    command.downgrade(alembic_cfg, SEBELUM)
    akhiran = uuid.uuid4().hex[:8]
    ganda = f"QA204 Rollback {akhiran}"
    with engine.begin() as conn:
        admin = _admin(conn)
        terhapus = _produk_lama(conn, admin, ganda, terhapus=True)
        otomatis = _produk_lama(conn, admin, ganda)
    command.upgrade(alembic_cfg, "head")
    manual = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_products (id, name, measurement_type, vendor_name, site_url, "
                "is_active, created_by, created_at, updated_at) VALUES (:id, :nama, 'manual', "
                "'Penyedia', 'https://contoh.test', true, :admin, now(), now())"
            ),
            {"id": manual, "nama": f"QA204 Manual {akhiran}", "admin": admin},
        )

    # Act
    command.downgrade(alembic_cfg, SEBELUM)

    # Assert
    with engine.connect() as conn:
        assert _nama(conn, manual) is None
        assert _nama(conn, otomatis) == ganda
        assert _nama(conn, terhapus) == f"{ganda} (dihapus {str(terhapus)[:8]})"
        kolom = conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'ai_products' AND column_name = 'measurement_type'"
            )
        )
    assert kolom == 0
