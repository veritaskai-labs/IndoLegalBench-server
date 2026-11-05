"""SCRUM-214: database menolak perpindahan status percobaan di luar D4 bagian B.

#97 "Part 2" butir 4. Status percobaan ditulis oleh beberapa jalur (worker #98, coba ulang
#99, pemulihan restart SCRUM-217), tidak semuanya lewat satu fungsi Python. Trigger
attempts_guard_status menegakkan tabel yang sama dengan
app.modules.runs.attempt_transitions untuk percobaan otomatis, di jalur tulis mana pun:
- pending -> running, running -> succeeded | technical_failure, technical_failure -> pending.
- UPDATE yang tidak mengubah status (mis. menulis model_version) tidak diperiksa.
- Percobaan manual (PBI-22) belum diatur.

upgrade() dan downgrade() dijalankan langsung di koneksi tes lewat Operations.context,
jadi DDL ikut di-rollback fixture. Dilewati kalau TEST_POSTGRES_URL kosong.

Partisi masukan (E3): 12 pasangan status yang berbeda untuk percobaan otomatis, UPDATE
tanpa perubahan status, percobaan manual, dan revisi turun/naik.
Label: Positif = diterima, Negatif = ditolak, Edge = batas perilaku.
"""

import importlib
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.modules.runs.models import AttemptStatus
from tests.measurement_pg import (
    CHECK_VIOLATION,
    buat_admin,
    buat_item_snapshot,
    buat_produk,
    buat_sesi,
    sqlstate_penolakan,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
MIGRASI = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "e7a41c2b9d53_attempt_status_guard.py"
)
MIGRASI_217 = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "63745ac5e515_allow_running_to_pending_attempt_.py"
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

SAH = {
    ("pending", "running"),
    ("running", "succeeded"),
    ("running", "technical_failure"),
    ("running", "pending"),
    ("technical_failure", "pending"),
}
PASANGAN = [
    pytest.param(asal.value, tujuan.value, id=f"{asal}->{tujuan}")
    for asal in AttemptStatus
    for tujuan in AttemptStatus
    if asal != tujuan
]

# Jalan sah dari pending ke setiap status, untuk menyiapkan keadaan awal.
_JALAN = {
    "pending": [],
    "running": ["running"],
    "succeeded": ["running", "succeeded"],
    "technical_failure": ["running", "technical_failure"],
}
UBAH_STATUS = "UPDATE attempts SET status = CAST(:status AS attempt_status_enum) WHERE id = :id"


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
def percobaan(conn):
    """Buat percobaan (otomatis atau manual) lalu bawa ke status awal lewat jalan yang sah."""
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(
        conn, buat_produk(conn, admin_id), admin_id, session_type="automatic", repetitions=3
    )
    item_id = buat_item_snapshot(conn, sesi_id, admin_id)
    nomor = iter(range(1, 4))

    def buat(status: str = "pending", source: str = "automatic") -> uuid.UUID:
        percobaan_id = conn.execute(
            text(
                "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source) "
                "VALUES (:id, :sesi, :item, :nomor, CAST(:source AS attempt_source_enum)) "
                "RETURNING id"
            ),
            {
                "id": uuid.uuid4(),
                "sesi": sesi_id,
                "item": item_id,
                "nomor": next(nomor),
                "source": source,
            },
        ).scalar_one()
        for langkah in _JALAN[status]:
            _ubah(conn, percobaan_id, langkah)
        return percobaan_id

    return buat


def _ubah(conn, percobaan_id: uuid.UUID, status: str) -> None:
    conn.execute(text(UBAH_STATUS), {"status": status, "id": percobaan_id})


def _status(conn, percobaan_id: uuid.UUID) -> str:
    return conn.execute(
        text("SELECT status::text FROM attempts WHERE id = :id"), {"id": percobaan_id}
    ).scalar_one()


def _jalankan(conn, arah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_214_attempt", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, arah)()


def _jalankan_217(conn, arah: str) -> None:
    spec = importlib.util.spec_from_file_location("migrasi_217_attempt", MIGRASI_217)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, arah)()


@pytest.mark.parametrize(("asal", "tujuan"), PASANGAN)
def test_database_menegakkan_tabel_d4_untuk_percobaan_otomatis(conn, percobaan, asal, tujuan):
    """Positif/Negatif: empat pasangan D4 diterima, delapan lainnya ditolak check_violation."""
    # Arrange
    percobaan_id = percobaan(asal)

    if (asal, tujuan) in SAH:
        # Act
        _ubah(conn, percobaan_id, tujuan)

        # Assert
        assert _status(conn, percobaan_id) == tujuan
        return

    # Act
    sqlstate = sqlstate_penolakan(conn, UBAH_STATUS, {"status": tujuan, "id": percobaan_id})

    # Assert
    assert sqlstate == CHECK_VIOLATION
    assert _status(conn, percobaan_id) == asal


def test_update_tanpa_perubahan_status_tidak_diperiksa(conn, percobaan):
    """Edge: mengubah kolom lain pada percobaan gagal (status ditulis sama) tetap boleh."""
    # Arrange
    percobaan_id = percobaan("technical_failure")

    # Act
    conn.execute(
        text(
            "UPDATE attempts SET status = 'technical_failure', "
            "model_version = 'model-uji-2026-11' WHERE id = :id"
        ),
        {"id": percobaan_id},
    )

    # Assert
    assert _status(conn, percobaan_id) == "technical_failure"


def test_percobaan_manual_belum_diatur(conn, percobaan):
    """Edge: pencatatan produk manual (PBI-22) boleh langsung pending -> succeeded."""
    # Arrange
    percobaan_id = percobaan(source="manual")

    # Act
    _ubah(conn, percobaan_id, "succeeded")

    # Assert
    assert _status(conn, percobaan_id) == "succeeded"


def test_salinan_tabel_di_migrasi_sama_dengan_aturan_aplikasi():
    """Edge: migrasi 217 menyimpan salinan beku; saat dibuat isinya sama dengan tabel aplikasi."""
    # Arrange
    spec = importlib.util.spec_from_file_location("migrasi_217_attempt_tabel", MIGRASI_217)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    aturan = importlib.import_module("app.modules.runs.attempt_transitions")

    # Act
    dari_aplikasi = {
        (asal.value, tujuan.value)
        for asal, semua in aturan.ALLOWED_ATTEMPT_TRANSITIONS.items()
        for tujuan in semua
    }

    # Assert
    assert set(modul.ALLOWED) == dari_aplikasi == SAH


def test_turun_revisi_217_menolak_kembali_running_ke_pending(conn, percobaan):
    """Positif: downgrade 217 mengembalikan trigger lama yang menolak running -> pending."""
    percobaan_id = percobaan("running")

    # Act
    _jalankan_217(conn, "downgrade")
    sqlstate = sqlstate_penolakan(conn, UBAH_STATUS, {"status": "pending", "id": percobaan_id})

    # Assert
    assert sqlstate == CHECK_VIOLATION

    # Cleanup: kembalikan upgrade 217
    _jalankan_217(conn, "upgrade")


def test_turun_revisi_melepas_penjaga_naik_lagi_memasangnya(conn, percobaan):
    """Positif: downgrade menghapus trigger dan fungsinya; upgrade memasangnya kembali."""
    # Arrange
    _jalankan(conn, "downgrade")
    lepas = percobaan()

    # Act
    _ubah(conn, lepas, "succeeded")
    _jalankan(conn, "upgrade")
    dijaga = percobaan()
    sqlstate = sqlstate_penolakan(conn, UBAH_STATUS, {"status": "succeeded", "id": dijaga})

    # Assert
    assert _status(conn, lepas) == "succeeded"
    assert sqlstate == CHECK_VIOLATION
