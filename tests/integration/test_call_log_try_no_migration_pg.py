"""SCRUM-77 SCRUM-218: migrasi nomor upaya di call_logs, dan bisa dibalik.

D2 call_logs punya try_no (1 = awal) dan is_manual_retry. Baris lama diisi ulang
urut waktu per attempt_id, supaya UNIQUE (attempt_id, try_no) bisa dipasang
walau sudah ada lebih dari satu baris untuk attempt yang sama.

upgrade() dan downgrade() dijalankan langsung di koneksi tes lewat
Operations.context, jadi semua DDL ikut di-rollback fixture. Dilewati kalau
TEST_POSTGRES_URL kosong.

Label: Positif = perilaku yang diharapkan, Negatif = ditolak database.
"""

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
MIGRASI = next(
    (Path(__file__).resolve().parents[2] / "migrations" / "versions").glob("*_call_log_try_no.py"),
    None,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

JAM = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


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


def _jalankan(conn, langkah: str) -> None:
    assert MIGRASI is not None, "migrasi *_call_log_try_no.py belum ada"
    spec = importlib.util.spec_from_file_location("migrasi_218", MIGRASI)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modul, langkah)()


def _kolom(conn) -> set[str]:
    return set(
        conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'call_logs'"
            )
        ).scalars()
    )


def _sisip(conn, attempt_id: uuid.UUID, menit: int, **kolom) -> uuid.UUID:
    baris_id = uuid.uuid4()
    nilai = {
        "id": baris_id,
        "attempt_id": attempt_id,
        "product_id": uuid.uuid4(),
        "started_at": JAM + timedelta(minutes=menit),
        "ended_at": JAM + timedelta(minutes=menit),
        "duration_ms": 0,
        "usd_idr_rate": 16000,
        "model_version": "deepseek-flash",
        "result": "no_response",
        "raw": "{}",
        **kolom,
    }
    conn.execute(
        text(
            f"INSERT INTO call_logs ({', '.join(nilai)}) "
            f"VALUES ({', '.join(':' + nama for nama in nilai)})"
        ),
        nilai,
    )
    return baris_id


def test_head_punya_nomor_upaya_dengan_bawaan_upaya_pertama(conn):
    """Positif: baris baru tanpa isian try_no adalah upaya ke-1, bukan coba ulang."""
    # Arrange
    baris_id = _sisip(conn, uuid.uuid4(), 0)

    # Act
    try_no, ulang = conn.execute(
        text("SELECT try_no, is_manual_retry FROM call_logs WHERE id = :id"), {"id": baris_id}
    ).one()

    # Assert
    assert (try_no, ulang) == (1, False)


def test_upgrade_mengisi_ulang_baris_lama_urut_waktu_per_attempt(conn):
    """Positif: dua baris lama di attempt yang sama menjadi upaya 1 dan 2, yang ke-2 coba ulang."""
    # Arrange
    _jalankan(conn, "downgrade")
    attempt_id = uuid.uuid4()
    kedua = _sisip(conn, attempt_id, 5)
    pertama = _sisip(conn, attempt_id, 0)
    lain = _sisip(conn, uuid.uuid4(), 1)

    # Act
    _jalankan(conn, "upgrade")

    # Assert
    hasil = {
        baris_id: (try_no, ulang)
        for baris_id, try_no, ulang in conn.execute(
            text("SELECT id, try_no, is_manual_retry FROM call_logs WHERE id IN (:a, :b, :c)"),
            {"a": pertama, "b": kedua, "c": lain},
        )
    }
    assert hasil == {pertama: (1, False), kedua: (2, True), lain: (1, False)}


def test_nomor_upaya_ganda_ditolak_database(conn):
    """Negatif: UNIQUE (attempt_id, try_no), dua dispatch tidak bisa sama-sama upaya ke-1."""
    # Arrange
    attempt_id = uuid.uuid4()
    _sisip(conn, attempt_id, 0, try_no=1)

    # Act + Assert
    with pytest.raises(IntegrityError), conn.begin_nested():
        _sisip(conn, attempt_id, 1, try_no=1)


def test_nomor_upaya_nol_ditolak_database(conn):
    """Negatif: CHECK try_no >= 1, sesuai D2 "1 = awal"."""
    # Act + Assert
    with pytest.raises(IntegrityError), conn.begin_nested():
        _sisip(conn, uuid.uuid4(), 0, try_no=0)


def test_downgrade_menghapus_kolom_nomor_upaya(conn):
    """Positif: rollback mengembalikan call_logs seperti SCRUM-209, data upaya tetap ada."""
    # Arrange
    baris_id = _sisip(conn, uuid.uuid4(), 0)

    # Act
    _jalankan(conn, "downgrade")

    # Assert
    assert not {"try_no", "is_manual_retry"} & _kolom(conn)
    assert (
        conn.execute(
            text("SELECT count(*) FROM call_logs WHERE id = :id"), {"id": baris_id}
        ).scalar_one()
        == 1
    )
