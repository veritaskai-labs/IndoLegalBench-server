"""Migrasi asal kasus, tabel impor, dan tag kosong pada draft di PostgreSQL. PBI-4 SCRUM-337.

Yang tidak bisa dibuktikan SQLite: CHECK dan enum di PostgreSQL, interaksi dengan trigger
case_versions_reject_approved_mutation, dan kedua fungsi isi-ulang di migrasi f2a7c4e91b38
(sidik jari kasus lama saat upgrade, tag kosong menjadi test saat downgrade).

Semua baris dibuat di dalam transaksi yang dibatalkan di akhir test.

Label: Positif = boleh, Negatif = ditolak database, Edge = data batas.
"""

import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.cases.fingerprint import content_fingerprint
from tests.measurement_pg import buat_admin

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "f2a7c4e91b38_case_origin_and_import_tables.py"
)


def _muat_migrasi():
    spec = importlib.util.spec_from_file_location("migrasi_asal_kasus", MIGRASI)
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


def _kasus(
    conn, admin_id, *, isi: dict, split_tag: str | None = "test", status: str = "draft"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Satu kasus dengan satu versi. cases dan case_versions saling menunjuk (pola PBI-8)."""
    suite_id, case_id, version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    kode = f"ASL-{case_id.hex[:8]}"
    conn.execute(
        text(
            "INSERT INTO suites (id, name, status, created_by) "
            "VALUES (:id, :name, 'active', :admin_id)"
        ),
        {"id": suite_id, "name": f"Suite {suite_id.hex[:8]}", "admin_id": admin_id},
    )
    conn.execute(
        text(
            "INSERT INTO case_versions "
            "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
            "VALUES (:id, :case_id, 1, CAST(:status AS case_status_enum), "
            "CAST(:split_tag AS case_split_tag_enum), CAST(:isi AS jsonb), :admin_id, :kode)"
        ),
        {
            "id": version_id,
            "case_id": case_id,
            "status": status,
            "split_tag": split_tag,
            "isi": json.dumps(isi),
            "admin_id": admin_id,
            "kode": kode,
        },
    )
    conn.execute(
        text(
            "INSERT INTO cases (id, case_code, suite_id, created_by, updated_by, "
            "current_version_id) VALUES (:id, :kode, :suite_id, :admin_id, :admin_id, :versi)"
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


def _kolom(conn, tabel: str, kolom: str, id_: uuid.UUID):
    return conn.scalar(text(f"SELECT {kolom} FROM {tabel} WHERE id = :id"), {"id": id_})


def test_kasus_lama_tercatat_manual_tanpa_batch_dan_kasus_asal(conn):
    """Positif: D8, server_default mengisi kasus yang sudah ada sebagai input manual."""
    # Arrange
    case_id, _ = _kasus(conn, buat_admin(conn), isi={"title": "PHK"})

    # Act
    asal = conn.execute(
        text("SELECT origin::text, import_batch_id, source_case_id FROM cases WHERE id = :id"),
        {"id": case_id},
    ).one()

    # Assert
    assert tuple(asal) == ("manual", None, None)


def test_backfill_sidik_jari_sama_dengan_aturan_aplikasi(conn):
    """Positif: upgrade mengisi sidik jari kasus lama dengan rumus yang sama dengan aplikasi,
    supaya impor pertama mengenali kasus lama yang kembar; kasus kosong tetap NULL."""
    # Arrange
    admin = buat_admin(conn)
    berisi, _ = _kasus(
        conn, admin, isi={"title": " PHK  Sepihak", "question": "Apakah PHK\ttanpa surat sah?"}
    )
    kosong, _ = _kasus(conn, admin, isi={"title": "", "question": None})

    # Act
    _muat_migrasi().isi_sidik_jari(conn)

    # Assert
    assert _kolom(conn, "cases", "content_fingerprint", berisi) == content_fingerprint(
        "phk sepihak", "apakah phk tanpa surat sah?"
    )
    assert _kolom(conn, "cases", "content_fingerprint", kosong) is None


def test_draft_tanpa_tag_boleh_disimpan(conn):
    """Positif: AC8, kasus hasil impor atau duplikat boleh disimpan tanpa tag selama draft."""
    # Act
    _, versi = _kasus(conn, buat_admin(conn), isi={"title": "Impor"}, split_tag=None)

    # Assert
    assert _kolom(conn, "case_versions", "split_tag", versi) is None


@pytest.mark.parametrize("status", ["in_review", "needs_revision", "approved"])
def test_versi_yang_lewat_draft_wajib_bertag(conn, status):
    """Negatif: D8 CHECK, hanya draft yang boleh tanpa tag."""
    # Act / Assert
    with pytest.raises(IntegrityError, match="ck_case_versions_split_tag_required_after_draft"):
        _kasus(conn, buat_admin(conn), isi={"title": "X"}, split_tag=None, status=status)


def test_versi_approved_tetap_terkunci_oleh_trigger(conn):
    """Negatif: membuat tag nullable tidak membuka isi versi approved untuk diubah."""
    # Arrange
    _, versi = _kasus(conn, buat_admin(conn), isi={"title": "Final"}, status="approved")

    # Act / Assert
    with pytest.raises(DBAPIError, match="approved case version is immutable"):
        conn.execute(
            text("UPDATE case_versions SET content = '{}'::jsonb WHERE id = :id"), {"id": versi}
        )


@pytest.mark.parametrize(
    ("asal", "constraint"),
    [
        # Negatif: kasus impor tanpa batch.
        ("import", "ck_cases_import_batch_matches_origin"),
        # Negatif: kasus duplikat tanpa kasus asal.
        ("duplicate", "ck_cases_source_case_matches_origin"),
    ],
)
def test_asal_kasus_tanpa_rujukannya_ditolak(conn, asal, constraint):
    """Negatif: D8, asal impor dan duplikat selalu berpasangan dengan rujukannya."""
    # Arrange
    case_id, _ = _kasus(conn, buat_admin(conn), isi={"title": "X"})

    # Act / Assert
    with pytest.raises(IntegrityError, match=constraint):
        conn.execute(
            text("UPDATE cases SET origin = CAST(:asal AS case_origin_enum) WHERE id = :id"),
            {"asal": asal, "id": case_id},
        )


def test_downgrade_mengisi_tag_kosong_dengan_test_tanpa_mengubah_yang_lain(conn):
    """Edge: rollback mengembalikan NOT NULL dengan mengisi draft tanpa tag sebagai test,
    tertutup untuk Developer AI (D9 aturan 4); versi yang sudah bertag tidak berubah."""
    # Arrange
    admin = buat_admin(conn)
    _, tanpa_tag = _kasus(conn, admin, isi={"title": "Impor"}, split_tag=None)
    _, dev = _kasus(conn, admin, isi={"title": "Dev"}, split_tag="dev")

    # Act
    terisi = _muat_migrasi().isi_tag_kosong(conn)

    # Assert
    assert terisi == 1
    assert _kolom(conn, "case_versions", "split_tag::text", tanpa_tag) == "test"
    assert _kolom(conn, "case_versions", "split_tag::text", dev) == "dev"
