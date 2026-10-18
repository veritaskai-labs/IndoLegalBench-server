"""Tag versi approved boleh diubah, isinya tetap tidak. PBI-9 AC3, SCRUM-164.

D4a aturan 1: kolom isi baris approved tidak boleh di-UPDATE, kecuali
split_tag oleh Admin. Trigger hanya mengizinkan split_tag (dan updated_at
yang ikut berubah); siapa yang boleh mengubahnya dijaga cases.service.
Larangan lain tetap diuji di test_case_versions_immutable_pg.py.
"""

import pytest
from sqlalchemy import text

from tests.integration.test_case_versions_immutable_pg import (  # noqa: F401  fixture
    PESAN_TRIGGER,
    POSTGRES_URL,
    _ditolak,
    kasus,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


def _tag(conn, version_id) -> str:
    return conn.execute(
        text("SELECT split_tag::text FROM case_versions WHERE id = :id"), {"id": version_id}
    ).scalar_one()


def test_tag_versi_approved_boleh_diubah(kasus):  # noqa: F811
    # Arrange
    conn, _, _, disetujui, _ = kasus

    # Act
    conn.execute(
        text("UPDATE case_versions SET split_tag = 'dev', updated_at = now() WHERE id = :id"),
        {"id": disetujui},
    )

    # Assert
    assert _tag(conn, disetujui) == "dev"


def test_tag_bersama_isi_tetap_ditolak(kasus):  # noqa: F811
    """Menyelipkan perubahan isi di UPDATE tag tidak lolos."""
    # Arrange
    conn, _, _, disetujui, _ = kasus

    # Act
    pesan = _ditolak(
        conn,
        "UPDATE case_versions SET split_tag = 'dev', case_code = 'QA-999' WHERE id = :id",
        {"id": disetujui},
    )

    # Assert
    assert PESAN_TRIGGER in pesan
    assert _tag(conn, disetujui) == "test"


def test_penanda_pernah_dev_bawaan_false(kasus):  # noqa: F811
    # Arrange
    conn, case_id, _, _, _ = kasus

    # Act
    nilai = conn.execute(
        text("SELECT was_dev FROM cases WHERE id = :id"), {"id": case_id}
    ).scalar_one()

    # Assert
    assert nilai is False


def test_kolom_baru_di_versi_approved_tetap_terkunci(kasus):  # noqa: F811
    """Review Roben di #53: trigger mengunci seluruh baris kecuali tag, bukan daftar kolom.

    Kolom yang ditambahkan nanti otomatis ikut terkunci. DDL di PostgreSQL ikut
    transaksi fixture, jadi kolomnya hilang lagi setelah test.
    """
    # Arrange
    conn, _, _, disetujui, _ = kasus
    # FK cases <-> case_versions ditunda; ALTER TABLE menolak tabel yang masih punya cek tertunda.
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    conn.execute(text("ALTER TABLE case_versions ADD COLUMN probe_note text"))

    # Act
    pesan = _ditolak(
        conn,
        "UPDATE case_versions SET probe_note = 'diubah' WHERE id = :id",
        {"id": disetujui},
    )

    # Assert
    assert PESAN_TRIGGER in pesan
