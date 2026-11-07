"""SCRUM-222 PR B: service versi rubrik di PostgreSQL, bersama trigger PR A.

Trigger rubrik hanya ada di PostgreSQL. Tes ini memastikan urutan tulis service
diterima trigger: versi masuk belum aktif, isinya ditambah, versi aktif lama
dinonaktifkan, lalu versi baru diaktifkan dan terkunci. Session ORM memakai savepoint
di dalam transaksi tes, jadi commit service tidak pernah tersimpan.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.grading import service
from app.modules.grading.schemas import RubricVersionCreate
from tests.measurement_pg import INSUFFICIENT_PRIVILEGE, buat_admin, sqlstate_penolakan
from tests.modules.grading.rubric_payload import body_rubrik

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
def db(conn):
    with Session(bind=conn, join_transaction_mode="create_savepoint") as sesi:
        yield sesi


def _buat(db, admin_id, **ubah):
    payload = RubricVersionCreate.model_validate(body_rubrik(**ubah))
    return service.create_version(db, payload, created_by=admin_id)


def test_dua_versi_berurutan_diterima_trigger_dan_hanya_terbaru_yang_aktif(conn, db):
    """Positif: AC7, D7. Trigger menerima urutan tulis service, satu versi aktif."""
    admin_id = buat_admin(conn)

    pertama = _buat(db, admin_id)
    kedua = _buat(db, admin_id)

    baris = conn.execute(
        text(
            "SELECT version_no, is_active, locked_at IS NOT NULL FROM rubric_versions "
            "WHERE created_by = :admin ORDER BY version_no"
        ),
        {"admin": admin_id},
    ).all()
    assert [tuple(b) for b in baris] == [(1, False, True), (2, True, True)]
    assert (pertama.version_no, kedua.version_no) == (1, 2)


def test_isi_versi_dari_service_tetap_terkunci_oleh_trigger(conn, db):
    """Negatif: setelah POST, isi versi tidak bisa ditambah lagi (versi baru wajib dibuat)."""
    versi = _buat(db, buat_admin(conn))

    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO rubric_levels (id, rubric_version_id, score, label, is_passing) "
        "VALUES (gen_random_uuid(), :id, 3, 'Tambahan', false)",
        {"id": versi.id},
    )

    assert sqlstate == INSUFFICIENT_PRIVILEGE
