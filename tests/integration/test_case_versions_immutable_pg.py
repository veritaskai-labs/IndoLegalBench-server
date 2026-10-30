"""SCRUM-139: PBI-8 AC1, versi approved kebal UPDATE dan DELETE di PostgreSQL."""

import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

PESAN_TRIGGER = "approved case version is immutable"
ISI_ASLI = {"identity": {"title": "PHK sepihak"}}


def _isi(nilai: dict) -> str:
    return json.dumps(nilai)


def _versi(conn, case_id, user_id, nomor: int, status: str) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO case_versions "
            "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
            "VALUES (:id, :case_id, :nomor, CAST(:status AS case_status_enum), 'test', "
            "CAST(:isi AS jsonb), :user_id, 'QA-139') RETURNING id"
        ),
        {
            "id": uuid.uuid4(),
            "case_id": case_id,
            "nomor": nomor,
            "status": status,
            "isi": _isi(ISI_ASLI),
            "user_id": user_id,
        },
    ).scalar_one()


@pytest.fixture
def kasus():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as conn:
        transaksi = conn.begin()
        user_id, suite_id, case_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO users (id, email, name, role) "
                "VALUES (:id, 'qa-139@veritask.test', 'QA 139', 'contributor')"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO suites (id, name, status, created_by) "
                "VALUES (:id, 'Suite QA 139', 'active', :user_id)"
            ),
            {"id": suite_id, "user_id": user_id},
        )
        disetujui = _versi(conn, case_id, user_id, 1, "approved")
        draf = _versi(conn, case_id, user_id, 2, "draft")
        conn.execute(
            text(
                "INSERT INTO cases (id, case_code, suite_id, created_by, updated_by, "
                "current_version_id, latest_approved_version_id) "
                "VALUES (:id, 'QA-139', :suite_id, :user_id, :user_id, :draf, :disetujui)"
            ),
            {
                "id": case_id,
                "suite_id": suite_id,
                "user_id": user_id,
                "draf": draf,
                "disetujui": disetujui,
            },
        )
        try:
            yield conn, case_id, user_id, disetujui, draf
        finally:
            transaksi.rollback()
    engine.dispose()


def _ditolak(conn, perintah: str, params: dict) -> str:
    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as galat:
        conn.execute(text(perintah), params)
    savepoint.rollback()
    return str(galat.value.orig)


def _baris(conn, version_id) -> dict:
    return dict(
        conn.execute(
            text(
                "SELECT status::text AS status, content, case_code FROM case_versions WHERE id = :id"
            ),
            {"id": version_id},
        )
        .mappings()
        .one()
    )


@pytest.mark.parametrize(
    "perintah",
    [
        pytest.param(
            'UPDATE case_versions SET content = \'{"identity": {"title": "Diubah"}}\' '
            "WHERE id = :id",
            id="ubah-isi",
        ),
        pytest.param(
            "UPDATE case_versions SET status = 'draft' WHERE id = :id",
            id="kembalikan-ke-draft",
        ),
        pytest.param(
            "UPDATE case_versions SET case_code = 'QA-999' WHERE id = :id",
            id="ganti-kode-kasus",
        ),
        pytest.param("DELETE FROM case_versions WHERE id = :id", id="hapus"),
    ],
)
def test_versi_approved_menolak_perubahan_dan_isinya_tetap(kasus, perintah):
    conn, _, _, disetujui, _ = kasus

    pesan = _ditolak(conn, perintah, {"id": disetujui})

    assert PESAN_TRIGGER in pesan
    assert _baris(conn, disetujui) == {
        "status": "approved",
        "content": ISI_ASLI,
        "case_code": "QA-139",
    }


def test_draf_tetap_boleh_disunting(kasus):
    conn, _, _, _, draf = kasus

    conn.execute(
        text("UPDATE case_versions SET content = CAST(:isi AS jsonb) WHERE id = :id"),
        {"isi": _isi({"identity": {"title": "Disunting"}}), "id": draf},
    )

    assert _baris(conn, draf)["content"] == {"identity": {"title": "Disunting"}}


def test_draf_tetap_boleh_disetujui(kasus):
    conn, _, _, _, draf = kasus

    conn.execute(text("UPDATE case_versions SET status = 'approved' WHERE id = :id"), {"id": draf})

    assert _baris(conn, draf)["status"] == "approved"


def test_versi_baru_tetap_boleh_ditambahkan_setelah_ada_yang_approved(kasus):
    conn, case_id, user_id, _, _ = kasus

    baru = _versi(conn, case_id, user_id, 3, "draft")

    assert _baris(conn, baru)["status"] == "draft"
