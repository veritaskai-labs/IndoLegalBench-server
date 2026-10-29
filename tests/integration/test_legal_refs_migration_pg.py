"""SCRUM-159: tabel rujukan hukum dan salinan data Sprint 1 di PostgreSQL.

Yang tidak bisa dibuktikan SQLite: foreign key ke regulation_types, seed 266
jenis dari migrasi, dan salinan rujukan lama dari JSONB case_versions.content
tanpa mengubah content (versi approved terkunci oleh PBI-8).

Semua baris dibuat di dalam transaksi yang dibatalkan di akhir test.
"""

import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "d4f7a2c18b36_create_legal_reference_tables.py"
)

KTK_001 = {
    "title": "PHK",
    "legal_refs": [
        {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "156"}
    ],
}
CAMPURAN = {
    "title": "Campuran",
    "legal_refs": [
        {"regulation_type": "UU", "regulation_number": "6", "year": None, "pasal": "II"},
        {
            "regulation_type": "Peraturan Gubernur Kuno",
            "regulation_number": "5",
            "year": 2001,
            "pasal": "3",
            "ayat": "2",
            "huruf": "a",
        },
        "Pasal 1 UU lama",
        None,
        "",
        {},
    ],
}


def _muat_migrasi():
    spec = importlib.util.spec_from_file_location("migrasi_legal_refs", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def conn():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            # Migrasi sudah menyalin kasus yang ada di database ini. Kosongkan di dalam
            # transaksi (dibatalkan di akhir) supaya salinan ulang tidak bentrok.
            koneksi.execute(text("DELETE FROM legal_ref_links"))
            koneksi.execute(text("DELETE FROM legal_refs"))
            yield koneksi
        finally:
            transaksi.rollback()
    engine.dispose()


def _versi(conn, content: dict, status: str = "draft") -> uuid.UUID:
    """Satu kasus dengan satu versi berisi content lama, sama seperti data Sprint 1."""
    user_id, suite_id, case_id, version_id = (uuid.uuid4() for _ in range(4))
    conn.execute(
        text(
            "INSERT INTO users (id, email, name, role) VALUES (:id, :email, 'QA 159', 'contributor')"
        ),
        {"id": user_id, "email": f"qa-159-{user_id}@veritask.test"},
    )
    conn.execute(
        text(
            "INSERT INTO suites (id, name, status, created_by) "
            "VALUES (:id, :nama, 'active', :user_id)"
        ),
        {"id": suite_id, "nama": f"Suite {suite_id}", "user_id": user_id},
    )
    conn.execute(
        text(
            "INSERT INTO case_versions "
            "(id, case_id, version_no, status, split_tag, content, created_by, case_code) "
            "VALUES (:id, :case_id, 1, CAST(:status AS case_status_enum), 'test', "
            "CAST(:isi AS jsonb), :user_id, :kode)"
        ),
        {
            "id": version_id,
            "case_id": case_id,
            "status": status,
            "isi": json.dumps(content),
            "user_id": user_id,
            "kode": f"QA-{str(case_id)[:8]}",
        },
    )
    conn.execute(
        text(
            "INSERT INTO cases (id, case_code, suite_id, created_by, updated_by, "
            "current_version_id, latest_approved_version_id) "
            "VALUES (:id, :kode, :suite_id, :user_id, :user_id, :version_id, "
            "CASE WHEN :status = 'approved' THEN :version_id END)"
        ),
        {
            "id": case_id,
            "kode": f"QA-{str(case_id)[:8]}",
            "suite_id": suite_id,
            "user_id": user_id,
            "version_id": version_id,
            "status": status,
        },
    )
    return version_id


def _hasil(conn, version_id):
    return conn.execute(
        text(
            'SELECT r."order", r.kind, r.free_text, l.position, l.connector, l.form, '
            "l.regulation_type, l.number, l.year, l.pasal, l.ayat, l.huruf "
            "FROM legal_refs r LEFT JOIN legal_ref_links l ON l.legal_ref_id = r.id "
            'WHERE r.case_version_id = :id ORDER BY r."order"'
        ),
        {"id": version_id},
    ).all()


def _salin(conn):
    from app.modules.legal_refs import seed, type_lookup

    indeks = type_lookup.RegulationTypeIndex(seed.load_regulation_types())
    return _muat_migrasi().copy_legacy_refs(conn, indeks)


def test_migrasi_mengisi_266_jenis_peraturan(conn):
    # Act
    jumlah = conn.execute(text("SELECT count(*) FROM regulation_types")).scalar_one()
    aktif = conn.execute(text("SELECT count(*) FROM regulation_types WHERE is_active")).scalar_one()

    # Assert
    assert (jumlah, aktif) == (266, 266)
    singkatan = conn.execute(
        text("SELECT singkatan FROM regulation_types WHERE code = 'uu'")
    ).scalar_one()
    assert singkatan == "UU"


def test_rujukan_ktk_001_disalin_sama_seperti_sebelumnya(conn):
    # Arrange: Pasal 156 UU 13/2003 pada versi approved
    versi = _versi(conn, KTK_001, status="approved")

    # Act
    _salin(conn)

    # Assert
    assert _hasil(conn, versi) == [
        (1, "structured", None, 1, "none", "pasal", "uu", "13", 2003, "156", None, None)
    ]


def test_salinan_tidak_mengubah_content_versi_approved(conn):
    # Arrange
    versi = _versi(conn, KTK_001, status="approved")

    # Act
    _salin(conn)

    # Assert: key legal_refs masih ada dan isinya persis sama
    content = conn.execute(
        text("SELECT content FROM case_versions WHERE id = :id"), {"id": versi}
    ).scalar_one()
    assert content == KTK_001


def test_rujukan_campuran_disalin_dan_yang_tidak_cocok_jadi_teks_bebas(conn):
    # Arrange
    versi = _versi(conn, CAMPURAN)

    # Act
    jumlah = _salin(conn)

    # Assert
    assert _hasil(conn, versi) == [
        (1, "structured", None, 1, "none", "pasal", "uu", "6", None, "II", None, None),
        (
            2,
            "free_text",
            "Peraturan Gubernur Kuno Nomor 5 Tahun 2001, Pasal 3, Ayat 2, Huruf a",
            *([None] * 9),
        ),
        (3, "free_text", "Pasal 1 UU lama", *([None] * 9)),
    ]
    assert jumlah["free_text_fallbacks"] >= 2
    assert jumlah["skipped"] >= 3


@pytest.mark.parametrize(
    "content",
    [{"title": "tanpa key"}, {"legal_refs": None}, {"legal_refs": []}, {"legal_refs": "teks"}],
)
def test_versi_tanpa_rujukan_yang_bisa_dibaca_tidak_menghasilkan_baris(conn, content):
    # Arrange
    versi = _versi(conn, content)

    # Act
    _salin(conn)

    # Assert
    assert _hasil(conn, versi) == []


def test_foreign_key_menolak_jenis_peraturan_di_luar_daftar(conn):
    # Arrange
    versi = _versi(conn, {"legal_refs": []})
    ref_id = uuid.uuid4()
    conn.execute(
        text(
            'INSERT INTO legal_refs (id, case_version_id, "order", kind) '
            "VALUES (:id, :versi, 1, 'structured')"
        ),
        {"id": ref_id, "versi": versi},
    )

    # Act + Assert
    with pytest.raises(IntegrityError, match="fk_legal_ref_links_regulation_type"):
        conn.execute(
            text(
                "INSERT INTO legal_ref_links "
                "(id, legal_ref_id, position, connector, form, regulation_type, number) "
                "VALUES (:id, :ref, 1, 'none', 'pasal', 'tidak_ada_di_daftar', '1')"
            ),
            {"id": uuid.uuid4(), "ref": ref_id},
        )


def test_database_menolak_link_pertama_dengan_penghubung(conn):
    # Arrange
    versi = _versi(conn, {"legal_refs": []})
    ref_id = uuid.uuid4()
    conn.execute(
        text(
            'INSERT INTO legal_refs (id, case_version_id, "order", kind) '
            "VALUES (:id, :versi, 1, 'structured')"
        ),
        {"id": ref_id, "versi": versi},
    )

    # Act + Assert
    with pytest.raises(IntegrityError, match="ck_legal_ref_links_connector_matches_position"):
        conn.execute(
            text(
                "INSERT INTO legal_ref_links "
                "(id, legal_ref_id, position, connector, form, regulation_type, number) "
                "VALUES (:id, :ref, 1, 'jo', 'pasal', 'uu', '13')"
            ),
            {"id": uuid.uuid4(), "ref": ref_id},
        )
