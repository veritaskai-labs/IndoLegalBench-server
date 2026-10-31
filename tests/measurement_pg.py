"""Bantuan tes PostgreSQL untuk tabel pengukuran PBI-11 (SCRUM-205).

Semua fungsi menerima koneksi yang transaksinya di-rollback oleh fixture tes,
jadi data yang dibuat di sini tidak pernah tersimpan.

Tes negatif memeriksa SQLSTATE, bukan sekadar "ada error": sebelum tabel ada,
setiap perintah juga gagal (undefined_table), dan itu tidak boleh terhitung lulus.
"""

import uuid
from collections.abc import Callable

import psycopg
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"
INVALID_TEXT_REPRESENTATION = "22P02"
STRING_DATA_RIGHT_TRUNCATION = "22001"
UNIQUE_VIOLATION = "23505"
INSUFFICIENT_PRIVILEGE = "42501"


def buat_admin(conn: Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, name, role) VALUES (:id, :email, 'Admin 205', 'admin')"
        ),
        {"id": user_id, "email": f"admin-205-{user_id.hex}@veritask.test"},
    )
    return user_id


def buat_produk(conn: Connection, admin_id: uuid.UUID) -> uuid.UUID:
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


def buat_sesi(conn: Connection, produk_id: uuid.UUID, admin_id: uuid.UUID, **ubah) -> uuid.UUID:
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


def ubah_status(conn: Connection, sesi_id: uuid.UUID, status: str) -> None:
    conn.execute(
        text(
            "UPDATE measurement_sessions "
            "SET status = CAST(:status AS measurement_session_status_enum) WHERE id = :id"
        ),
        {"id": sesi_id, "status": status},
    )


def galat(conn: Connection, aksi: Callable[[], object]) -> psycopg.Error:
    """Galat database dari aksi yang harus ditolak.

    Savepoint menjaga transaksi tes tetap bisa dipakai setelah penolakan.
    """
    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as info:
        aksi()
    savepoint.rollback()
    return info.value.orig


def sqlstate_penolakan(conn: Connection, perintah: str, params: dict) -> str:
    """SQLSTATE penolakan perintah SQL apa pun."""
    return galat(conn, lambda: conn.execute(text(perintah), params)).sqlstate


def buat_kasus(
    conn: Connection, admin_id: uuid.UUID, split_tag: str = "dev"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Satu kasus dengan satu versi approved. Mengembalikan (case_id, case_version_id).

    cases dan case_versions saling menunjuk, jadi versinya dibuat dulu (pola tes PBI-8).
    """
    suite_id, case_id, version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    kode = f"UJI-{case_id.hex[:8]}"
    conn.execute(
        text(
            "INSERT INTO suites (id, name, status, created_by) "
            "VALUES (:id, :name, 'active', :admin_id)"
        ),
        {"id": suite_id, "name": f"Suite kasus {suite_id.hex[:8]}", "admin_id": admin_id},
    )
    conn.execute(
        text(
            "INSERT INTO case_versions "
            "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
            "VALUES (:id, :case_id, 1, 'approved', CAST(:split_tag AS case_split_tag_enum), "
            "CAST(:isi AS jsonb), :admin_id, :kode)"
        ),
        {
            "id": version_id,
            "case_id": case_id,
            "split_tag": split_tag,
            "isi": '{"identity": {"title": "PHK sepihak"}}',
            "admin_id": admin_id,
            "kode": kode,
        },
    )
    conn.execute(
        text(
            "INSERT INTO cases (id, case_code, suite_id, created_by, updated_by, "
            "current_version_id, latest_approved_version_id) "
            "VALUES (:id, :kode, :suite_id, :admin_id, :admin_id, :versi, :versi)"
        ),
        {
            "id": case_id,
            "kode": kode,
            "suite_id": suite_id,
            "admin_id": admin_id,
            "versi": version_id,
        },
    )
    return case_id, version_id
