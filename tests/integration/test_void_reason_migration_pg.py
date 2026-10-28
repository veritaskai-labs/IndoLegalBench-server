"""SCRUM-197: end_reason assignment void lama diganti kode AssignmentVoidReason.

Sebelum SCRUM-197, penonaktifan reviewer menulis teks "Reviewer deactivated".
Sekarang end_reason void berisi kode (deactivated / role_changed) dan
ReassignmentAssignmentRead.void_reason memvalidasinya sebagai enum, jadi baris
lama di dev/prod harus diganti sebelum aplikasi membacanya. end_reason
assignment replaced adalah teks bebas Admin dan tidak boleh tersentuh.

Semua baris dibuat di dalam transaksi yang dibatalkan di akhir test.
"""

import importlib.util
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
    / "c7e19d0b5a42_void_end_reason_codes.py"
)
TEKS_LAMA = "Reviewer deactivated"


def _muat_migrasi():
    assert MIGRASI.exists(), f"migrasi {MIGRASI.name} belum ada"
    spec = importlib.util.spec_from_file_location("migrasi_void_reason", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


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
def round_id(conn) -> uuid.UUID:
    """Satu kasus in_review dengan satu round, cukup untuk menampung assignment."""
    user_id, suite_id, case_id, version_id, ronde = (uuid.uuid4() for _ in range(5))
    kode = f"QA-197-{str(case_id)[:8]}"
    langkah = [
        (
            "INSERT INTO users (id, email, name, role) VALUES (:user, :email, 'QA 197', 'author')",
            {"email": f"qa-197-{user_id}@veritask.test"},
        ),
        (
            "INSERT INTO suites (id, name, status, created_by) "
            "VALUES (:suite, :nama, 'active', :user)",
            {"nama": f"Suite {suite_id}"},
        ),
        (
            "INSERT INTO case_versions "
            "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
            "VALUES (:versi, :kasus, 1, 'in_review', 'dev', CAST('{}' AS jsonb), :user, :kode)",
            {},
        ),
        (
            "INSERT INTO cases (id, case_code, suite_id, created_by, updated_by, "
            "current_version_id) VALUES (:kasus, :kode, :suite, :user, :user, :versi)",
            {},
        ),
        (
            "INSERT INTO review_rounds (id, case_id, case_version_id, round_no, status) "
            "VALUES (:ronde, :kasus, :versi, 1, 'awaiting_assignment')",
            {},
        ),
    ]
    dasar = {
        "user": user_id,
        "suite": suite_id,
        "kasus": case_id,
        "versi": version_id,
        "ronde": ronde,
        "kode": kode,
    }
    for sql, ekstra in langkah:
        conn.execute(text(sql), {**dasar, **ekstra})
    return ronde


@pytest.fixture
def tugas(conn, round_id):
    """Buat satu assignment dengan reviewer baru; mengembalikan id-nya."""

    def _buat(status: str, end_reason: str | None) -> uuid.UUID:
        reviewer, assignment = uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO users (id, email, name, role) "
                "VALUES (:id, :email, 'QA reviewer', 'reviewer')"
            ),
            {"id": reviewer, "email": f"qa-197-rev-{reviewer}@veritask.test"},
        )
        conn.execute(
            text(
                "INSERT INTO review_assignments "
                "(id, review_round_id, reviewer_id, source, status, end_reason) "
                "VALUES (:id, :ronde, :reviewer, 'system', CAST(:status AS "
                "review_assignment_status_enum), :alasan)"
            ),
            {
                "id": assignment,
                "ronde": round_id,
                "reviewer": reviewer,
                "status": status,
                "alasan": end_reason,
            },
        )
        return assignment

    return _buat


def _alasan(conn, assignment_id: uuid.UUID) -> str | None:
    return conn.execute(
        text("SELECT end_reason FROM review_assignments WHERE id = :id"), {"id": assignment_id}
    ).scalar_one()


def test_upgrade_mengganti_teks_lama_void_dengan_kode(conn, tugas):
    # Arrange
    lama = tugas("void", TEKS_LAMA)
    sudah_kode = tugas("void", "role_changed")
    diganti = tugas("replaced", TEKS_LAMA)  # teks bebas Admin, kebetulan sama

    # Act
    jumlah = _muat_migrasi().void_reason_to_codes(conn)

    # Assert
    assert jumlah == 1
    assert _alasan(conn, lama) == "deactivated"
    assert _alasan(conn, sudah_kode) == "role_changed"
    assert _alasan(conn, diganti) == TEKS_LAMA


def test_downgrade_mengembalikan_teks_lama(conn, tugas):
    # Arrange
    nonaktif = tugas("void", "deactivated")
    ubah_peran = tugas("void", "role_changed")
    diganti = tugas("replaced", "deactivated")

    # Act
    jumlah = _muat_migrasi().void_reason_to_text(conn)

    # Assert: role_changed tidak punya padanan lama; aplikasi lama membacanya
    # sebagai teks biasa, jadi dibiarkan.
    assert jumlah == 1
    assert _alasan(conn, nonaktif) == TEKS_LAMA
    assert _alasan(conn, ubah_peran) == "role_changed"
    assert _alasan(conn, diganti) == "deactivated"


def test_upgrade_tanpa_baris_lama_tidak_mengubah_apa_pun(conn, tugas):
    tugas("void", "deactivated")

    assert _muat_migrasi().void_reason_to_codes(conn) == 0
