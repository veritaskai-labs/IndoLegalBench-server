"""SCRUM-205: snapshot kasus per sesi di PostgreSQL. PBI-11 AC6, D2.

case_snapshots: satu snapshot per sesi. case_snapshot_items: salinan persis setiap
kasus yang diukur, menunjuk kasus live dan versi approved yang dipakai. Keduanya
hanya boleh INSERT. Mengubah atau menghapus snapshot akan mengubah arti hasil
yang sudah ada, jadi database menolaknya untuk semua peran.

Bantuan dan alasan pemeriksaan SQLSTATE ada di tests/measurement_pg.py.
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.measurement_pg import (
    FOREIGN_KEY_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    INVALID_TEXT_REPRESENTATION,
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

ISI_KASUS = {
    "identity": {"title": "PHK sepihak", "category": "Ketenagakerjaan"},
    "question": "Apakah pekerja berhak atas pesangon?",
    "reference_answer": "Berhak, sesuai Pasal 156 UU 13/2003.",
}


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


def _item(conn, snapshot_id, case_id, version_id, **ubah) -> uuid.UUID:
    isi = {
        "id": uuid.uuid4(),
        "snapshot_id": snapshot_id,
        "case_id": case_id,
        "case_version_id": version_id,
        "case_code": "KTK-001",
        "title": ISI_KASUS["identity"]["title"],
        "question": ISI_KASUS["question"],
        "split_tag": "dev",
        "category": "Ketenagakerjaan",
        "reference_answer": ISI_KASUS["reference_answer"],
        "legal_refs": json.dumps([rujukan_ktk_001(), rujukan_ac10()]),
        "answer_criteria": json.dumps([{"criterion": "Menyebut pesangon"}]),
        "traps": json.dumps([]),
        "content": json.dumps(ISI_KASUS),
    }
    isi.update(ubah)
    return conn.execute(
        text(
            "INSERT INTO case_snapshot_items "
            "(id, snapshot_id, case_id, case_version_id, case_code, title, question, split_tag, "
            "category, reference_answer, legal_refs, answer_criteria, traps, content) "
            "VALUES (:id, :snapshot_id, :case_id, :case_version_id, :case_code, :title, "
            ":question, CAST(:split_tag AS case_split_tag_enum), :category, :reference_answer, "
            "CAST(:legal_refs AS jsonb), CAST(:answer_criteria AS jsonb), CAST(:traps AS jsonb), "
            "CAST(:content AS jsonb)) "
            "RETURNING id"
        ),
        isi,
    ).scalar_one()


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


def test_item_menyimpan_salinan_persis_termasuk_rujukan_terstruktur(conn, item_id):
    """AC6 + PBI-21: rujukan terstruktur dan rantai jo. ikut tersalin apa adanya."""
    baris = conn.execute(
        text(
            "SELECT case_code, question, split_tag::text, legal_refs, content "
            "FROM case_snapshot_items WHERE id = :id"
        ),
        {"id": item_id},
    ).one()

    assert tuple(baris) == (
        "KTK-001",
        ISI_KASUS["question"],
        "dev",
        [rujukan_ktk_001(), rujukan_ac10()],
        ISI_KASUS,
    )


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE case_snapshot_items SET question = 'Pertanyaan diubah' WHERE id = :id",
        "UPDATE case_snapshot_items SET legal_refs = '[]'::jsonb WHERE id = :id",
        # Corner: UPDATE yang tidak mengubah apa pun tetap ditolak, tanpa celah.
        "UPDATE case_snapshot_items SET question = question WHERE id = :id",
        "DELETE FROM case_snapshot_items WHERE id = :id",
        "TRUNCATE case_snapshot_items CASCADE",
    ],
    ids=[
        "ubah-pertanyaan",
        "kosongkan-rujukan",
        "update-tanpa-perubahan",
        "delete",
        "truncate-cascade",
    ],
)
def test_item_tidak_bisa_diubah_atau_dihapus(conn, item_id, perintah):
    sqlstate = sqlstate_penolakan(conn, perintah, {"id": item_id})

    assert sqlstate == INSUFFICIENT_PRIVILEGE
    pertanyaan = conn.execute(
        text("SELECT question FROM case_snapshot_items WHERE id = :id"), {"id": item_id}
    ).scalar_one()
    assert pertanyaan == ISI_KASUS["question"]


@pytest.mark.parametrize("kolom", ["case_id", "case_version_id"])
def test_kasus_atau_versi_yang_tidak_ada_ditolak(conn, snapshot_id, kasus, kolom):
    case_id, version_id = kasus
    ubah = {"case_id": case_id, "version_id": version_id}
    ubah["case_id" if kolom == "case_id" else "version_id"] = uuid.uuid4()

    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO case_snapshot_items "
        "(id, snapshot_id, case_id, case_version_id, case_code, title, question, split_tag, "
        "content) VALUES (:id, :snapshot, :case_id, :version_id, 'KTK-001', 'Judul', 'Tanya', "
        "'dev', '{}'::jsonb)",
        {"id": uuid.uuid4(), "snapshot": snapshot_id, **ubah},
    )

    assert sqlstate == FOREIGN_KEY_VIOLATION


def test_tag_split_hanya_dev_atau_test(conn, snapshot_id, kasus):
    sqlstate = sqlstate_penolakan(
        conn,
        "INSERT INTO case_snapshot_items "
        "(id, snapshot_id, case_id, case_version_id, case_code, title, question, split_tag, "
        "content) VALUES (:id, :snapshot, :kasus, :versi, 'KTK-001', 'Judul', 'Tanya', "
        "CAST('both' AS case_split_tag_enum), '{}'::jsonb)",
        {"id": uuid.uuid4(), "snapshot": snapshot_id, "kasus": kasus[0], "versi": kasus[1]},
    )

    assert sqlstate == INVALID_TEXT_REPRESENTATION


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

    pertanyaan = conn.execute(
        text("SELECT question FROM case_snapshot_items WHERE id = :id"), {"id": item_id}
    ).scalar_one()
    assert pertanyaan == ISI_KASUS["question"]
