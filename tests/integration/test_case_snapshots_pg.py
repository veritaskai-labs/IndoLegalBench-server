"""SCRUM-205: snapshot kasus per sesi di PostgreSQL. PBI-11 AC6, D2.

case_snapshots: satu snapshot per sesi. case_snapshot_items memakai bentuk PBI-8
(suite_snapshot_items): kasus live, versi approved yang disalin, dan body dari
cases.sections.snapshot_body(), supaya mekanisme salinan PBI-8 dipakai ulang
(Kak Aileen, 31 Okt). Keduanya hanya boleh INSERT. Mengubah atau menghapus snapshot akan mengubah arti hasil
yang sudah ada, jadi database menolaknya untuk semua peran.

Bantuan dan alasan pemeriksaan SQLSTATE ada di tests/measurement_pg.py.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.modules.cases.sections import snapshot_body
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.measurement_pg import (
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    UNIQUE_VIOLATION,
    buat_admin,
    buat_kasus,
    buat_produk,
    buat_sesi,
    sqlstate_penolakan,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

PERTANYAAN = "Apakah pekerja berhak atas pesangon?"
# Body persis seperti yang dibuat mekanisme snapshot PBI-8.
BODY = snapshot_body(
    case_code="KTK-001",
    version_no=1,
    status="approved",
    split_tag="dev",
    content={"title": "PHK sepihak", "question": PERTANYAAN, "category": "Ketenagakerjaan"},
    legal_refs=[rujukan_ktk_001(), rujukan_ac10()],
)
PERTANYAAN_DI_BODY = (
    "SELECT body -> 'sections' ->> 'identity.question' FROM case_snapshot_items WHERE id = :id"
)
TAMBAH_ITEM = (
    "INSERT INTO case_snapshot_items (id, snapshot_id, case_id, case_version_id, body) "
    "VALUES (:id, :snapshot, :case_id, :version_id, CAST(:body AS jsonb))"
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
def sesi_id(conn, admin_id) -> uuid.UUID:
    return buat_sesi(conn, buat_produk(conn, admin_id), admin_id)


@pytest.fixture
def snapshot_id(conn, sesi_id) -> uuid.UUID:
    return _snapshot(conn, sesi_id)


@pytest.fixture
def kasus(conn, admin_id) -> tuple[uuid.UUID, uuid.UUID]:
    return buat_kasus(conn, admin_id)


@pytest.fixture
def item_id(conn, snapshot_id, kasus) -> uuid.UUID:
    return _item(conn, snapshot_id, *kasus)


def _snapshot(conn, sesi_id) -> uuid.UUID:
    return conn.execute(
        text("INSERT INTO case_snapshots (id, session_id) VALUES (:id, :sesi) RETURNING id"),
        {"id": uuid.uuid4(), "sesi": sesi_id},
    ).scalar_one()


def _item(conn, snapshot_id, case_id, version_id) -> uuid.UUID:
    item_id = uuid.uuid4()
    conn.execute(
        text(TAMBAH_ITEM),
        {
            "id": item_id,
            "snapshot": snapshot_id,
            "case_id": case_id,
            "version_id": version_id,
            "body": json.dumps(BODY),
        },
    )
    return item_id


# case_snapshots


def test_snapshot_sesi_tercatat_waktunya(conn, snapshot_id):
    dibuat = conn.execute(
        text("SELECT created_at IS NOT NULL FROM case_snapshots WHERE id = :id"),
        {"id": snapshot_id},
    ).scalar_one()

    assert dibuat is True


def test_satu_sesi_hanya_punya_satu_snapshot(conn, sesi_id, snapshot_id):
    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO case_snapshots (id, session_id) VALUES (:id, :sesi)",
        {"id": uuid.uuid4(), "sesi": sesi_id},
    )

    assert sqlstate == UNIQUE_VIOLATION


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE case_snapshots SET created_at = now() - interval '1 day' WHERE id = :id",
        "DELETE FROM case_snapshots WHERE id = :id",
        "TRUNCATE case_snapshots CASCADE",
    ],
    ids=["update", "delete", "truncate-cascade"],
)
def test_snapshot_tidak_bisa_diubah_atau_dihapus(conn, snapshot_id, perintah):
    sqlstate = sqlstate_penolakan(conn, perintah, {"id": snapshot_id})

    assert sqlstate == INSUFFICIENT_PRIVILEGE


# case_snapshot_items


def test_item_menyimpan_body_snapshot_pbi8_apa_adanya(conn, item_id, kasus):
    """AC6 + PBI-21: body PBI-8 termasuk rujukan terstruktur dan rantai jo. tersalin utuh."""
    baris = conn.execute(
        text("SELECT case_id, case_version_id, body FROM case_snapshot_items WHERE id = :id"),
        {"id": item_id},
    ).one()

    assert tuple(baris) == (*kasus, BODY)


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE case_snapshot_items SET body = jsonb_set(body, '{sections,identity.question}', "
        "'\"Pertanyaan diubah\"') WHERE id = :id",
        "UPDATE case_snapshot_items SET body = '{}'::jsonb WHERE id = :id",
        # Corner: UPDATE yang tidak mengubah apa pun tetap ditolak, tanpa celah.
        "UPDATE case_snapshot_items SET body = body WHERE id = :id",
        "DELETE FROM case_snapshot_items WHERE id = :id",
        "TRUNCATE case_snapshot_items CASCADE",
    ],
    ids=[
        "ubah-pertanyaan",
        "kosongkan-body",
        "update-tanpa-perubahan",
        "delete",
        "truncate-cascade",
    ],
)
def test_item_tidak_bisa_diubah_atau_dihapus(conn, item_id, perintah):
    sqlstate = sqlstate_penolakan(conn, perintah, {"id": item_id})

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    pertanyaan = conn.execute(text(PERTANYAAN_DI_BODY), {"id": item_id}).scalar_one()
    assert pertanyaan == PERTANYAAN


@pytest.mark.parametrize("kolom", ["case_id", "version_id"])
def test_kasus_atau_versi_yang_tidak_ada_ditolak(conn, snapshot_id, kasus, kolom):
    case_id, version_id = kasus
    params = {
        "id": uuid.uuid4(),
        "snapshot": snapshot_id,
        "case_id": case_id,
        "version_id": version_id,
        "body": json.dumps(BODY),
    }
    params[kolom] = uuid.uuid4()

    sqlstate = sqlstate_penolakan(conn, TAMBAH_ITEM, params)

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_kasus_live_yang_ada_di_snapshot_tidak_bisa_dihapus_permanen(conn, kasus, item_id):
    """Corner: referensi ke kasus live tidak boleh hilang lewat CASCADE."""
    sqlstate = sqlstate_penolakan(conn, "DELETE FROM cases WHERE id = :id", {"id": kasus[0]})

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_mengubah_kasus_live_tidak_mengubah_snapshot(conn, admin_id, kasus, item_id):
    """AC6: versi baru kasus live tidak mengubah salinan yang sudah diukur."""
    case_id, _ = kasus
    conn.execute(
        text(
            "INSERT INTO case_versions "
            "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
            "VALUES (:id, :case_id, 2, 'draft', 'dev', "
            "'{\"question\": \"Pertanyaan baru\"}'::jsonb, :admin_id, 'KTK-001')"
        ),
        {"id": uuid.uuid4(), "case_id": case_id, "admin_id": admin_id},
    )

    pertanyaan = conn.execute(text(PERTANYAAN_DI_BODY), {"id": item_id}).scalar_one()
    assert pertanyaan == PERTANYAAN
