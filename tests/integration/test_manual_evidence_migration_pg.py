"""SCRUM-233: tabel bukti jawaban manual D9 di PostgreSQL, termasuk rollback-nya.

D9 (ERD Jawaban Manual), attempt_evidence:
- maksimal 3 bukti per attempt, dicek service dan trigger hitung (dua pengunggah
  bersamaan tidak bisa sama-sama lolos, karena trigger mengunci baris attempt);
- CHECK kolom berkas dan tautan sesuai jenis, tipe hanya PNG/JPG/PDF;
- bukti hanya untuk percobaan manual.
Baris bukti tidak bisa diubah, hanya ditambah atau dihapus (bukti yang salah dihapus lalu
diunggah ulang, dan keduanya tercatat di audit). Isi berkas ada di evidence_files.

downgrade() dijalankan di koneksi tes lewat Operations.context, jadi DDL ikut di-rollback
fixture. Dilewati kalau TEST_POSTGRES_URL kosong.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak, Edge = batas perilaku.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.measurement_pg import (
    CHECK_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    STRING_DATA_RIGHT_TRUNCATION,
    buat_admin,
    buat_item_snapshot,
    buat_produk,
    buat_sesi,
    sqlstate_penolakan,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

_TAMBAH_TAUTAN = (
    "INSERT INTO attempt_evidence (id, attempt_id, kind, url, uploaded_by) "
    "VALUES (:id, :attempt, 'link', :url, :admin)"
)
_TAMBAH_BERKAS = (
    "INSERT INTO attempt_evidence "
    "(id, attempt_id, kind, file_key, file_name, mime_type, size_bytes, uploaded_by) "
    "VALUES (:id, :attempt, 'file', :key, 'bukti.pdf', :mime, 10, :admin)"
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


def _percobaan(conn, *, source="manual"):
    admin = buat_admin(conn)
    sesi = buat_sesi(conn, buat_produk(conn, admin), admin, session_type=source)
    item = buat_item_snapshot(conn, sesi, admin)
    attempt = conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
            "VALUES (:id, :sesi, :item, 1, CAST(:source AS attempt_source_enum)) RETURNING id"
        ),
        {"id": uuid.uuid4(), "sesi": sesi, "item": item, "source": source},
    ).scalar_one()
    return attempt, admin


def _tautan(conn, attempt, admin, url="https://chat.example.com/share/1"):
    params = {"id": uuid.uuid4(), "attempt": attempt, "url": url, "admin": admin}
    conn.execute(text(_TAMBAH_TAUTAN), params)
    return params["id"]


def _migrasi():
    # Dicari lewat isi docstring, supaya tes tidak bergantung pada revision id acak.
    berkas = [p for p in VERSIONS.glob("*.py") if "SCRUM-233" in p.read_text()]
    assert len(berkas) == 1, berkas
    spec = importlib.util.spec_from_file_location("migrasi_233", berkas[0])
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_tiga_bukti_berkas_dan_tautan_diterima(conn):
    """Positif: AC7, D9 berkas dan tautan bercampur sampai 3 per jawaban."""
    # Arrange
    attempt, admin = _percobaan(conn)
    conn.execute(
        text("INSERT INTO evidence_files (key, content) VALUES ('k1', :isi)"),
        {"isi": b"%PDF-1.7"},
    )

    # Act
    conn.execute(
        text(_TAMBAH_BERKAS),
        {
            "id": uuid.uuid4(),
            "attempt": attempt,
            "key": "k1",
            "mime": "application/pdf",
            "admin": admin,
        },
    )
    _tautan(conn, attempt, admin)
    _tautan(conn, attempt, admin)

    # Assert
    jumlah = conn.execute(
        text("SELECT count(*) FROM attempt_evidence WHERE attempt_id = :a"), {"a": attempt}
    ).scalar_one()
    assert jumlah == 3


def test_bukti_keempat_ditolak_trigger(conn):
    """Negatif: AC7, D9 trigger hitung menolak bukti keempat walau service terlewati."""
    # Arrange
    attempt, admin = _percobaan(conn)
    for _ in range(3):
        _tautan(conn, attempt, admin)

    # Act
    sqlstate = sqlstate_penolakan(
        conn,
        _TAMBAH_TAUTAN,
        {"id": uuid.uuid4(), "attempt": attempt, "url": "https://x.test", "admin": admin},
    )

    # Assert
    assert sqlstate == CHECK_VIOLATION


def test_bukti_dihapus_membuka_tempat_lagi(conn):
    """Edge: AC7, bukti yang salah dihapus lalu diganti; batas tetap 3 yang aktif."""
    # Arrange
    attempt, admin = _percobaan(conn)
    ids = [_tautan(conn, attempt, admin) for _ in range(3)]

    # Act
    conn.execute(text("DELETE FROM attempt_evidence WHERE id = :id"), {"id": ids[0]})
    _tautan(conn, attempt, admin)

    # Assert
    jumlah = conn.execute(
        text("SELECT count(*) FROM attempt_evidence WHERE attempt_id = :a"), {"a": attempt}
    ).scalar_one()
    assert jumlah == 3


def test_bukti_untuk_percobaan_otomatis_ditolak(conn):
    """Negatif: AC7, bukti hanya untuk jawaban manual; jawaban otomatis punya call_logs."""
    # Arrange
    attempt, admin = _percobaan(conn, source="automatic")

    # Act
    sqlstate = sqlstate_penolakan(
        conn,
        _TAMBAH_TAUTAN,
        {"id": uuid.uuid4(), "attempt": attempt, "url": "https://x.test", "admin": admin},
    )

    # Assert
    assert sqlstate == CHECK_VIOLATION


def test_bukti_tidak_bisa_diubah(conn):
    """Negatif: AC7, bukti tidak diedit diam-diam; ganti = hapus lalu tambah (tercatat audit)."""
    # Arrange
    attempt, admin = _percobaan(conn)
    bukti = _tautan(conn, attempt, admin)

    # Act
    sqlstate = sqlstate_penolakan(
        conn,
        "UPDATE attempt_evidence SET url = 'https://lain.test' WHERE id = :id",
        {"id": bukti},
    )

    # Assert
    assert sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize(
    ("perintah", "params"),
    [
        # Negatif: tipe di luar D9.
        (_TAMBAH_BERKAS, {"key": "k", "mime": "image/gif"}),
        # Negatif: tautan tanpa url.
        (_TAMBAH_TAUTAN, {"url": None}),
    ],
    ids=["gif", "tautan-kosong"],
)
def test_kolom_bukti_dijaga_check(conn, perintah, params):
    """Negatif: AC7, CHECK D9 jenis dan tipe bukti."""
    # Arrange
    attempt, admin = _percobaan(conn)

    # Act
    sqlstate = sqlstate_penolakan(
        conn, perintah, {"id": uuid.uuid4(), "attempt": attempt, "admin": admin, **params}
    )

    # Assert
    assert sqlstate == CHECK_VIOLATION


def test_tautan_lebih_dari_2048_karakter_ditolak(conn):
    """Edge: AC7, D9 url varchar(2048)."""
    # Arrange
    attempt, admin = _percobaan(conn)

    # Act
    sqlstate = sqlstate_penolakan(
        conn,
        _TAMBAH_TAUTAN,
        {
            "id": uuid.uuid4(),
            "attempt": attempt,
            "url": "https://x.test/" + "a" * 2040,
            "admin": admin,
        },
    )

    # Assert
    assert sqlstate == STRING_DATA_RIGHT_TRUNCATION


def test_downgrade_menolak_selama_ada_bukti(conn):
    """Negatif: rollback tidak boleh membuang bukti yang sudah diunggah."""
    # Arrange
    attempt, admin = _percobaan(conn)
    _tautan(conn, attempt, admin)

    # Act / Assert
    with (
        Operations.context(MigrationContext.configure(conn)),
        pytest.raises(RuntimeError, match="evidence"),
    ):
        _migrasi().downgrade()


def test_downgrade_tanpa_bukti_menghapus_tabel(conn):
    """Positif: rollback bersih saat belum ada bukti; trigger dan enum ikut hilang."""
    # Act
    with Operations.context(MigrationContext.configure(conn)):
        _migrasi().downgrade()

    # Assert
    sisa = conn.execute(
        text(
            "SELECT count(*) FROM pg_class WHERE relname IN ('attempt_evidence', 'evidence_files')"
        )
    ).scalar_one()
    fungsi = conn.execute(
        text("SELECT count(*) FROM pg_proc WHERE proname LIKE 'attempt_evidence_%'")
    ).scalar_one()
    jenis = conn.execute(
        text("SELECT count(*) FROM pg_type WHERE typname = 'attempt_evidence_kind_enum'")
    ).scalar_one()
    assert (sisa, fungsi, jenis) == (0, 0, 0)
