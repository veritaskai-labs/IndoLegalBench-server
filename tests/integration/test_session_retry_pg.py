"""Coba ulang yang gagal di PostgreSQL sungguhan. SCRUM-77 SCRUM-218.

PBI-12 AC3 di atas trigger dan index asli: attempts_protect_answer (#89) harus
membolehkan technical_failure -> pending karena jawaban gagal tidak disimpan
(#98), partial index "satu sesi otomatis running" menolak bila sesi lain
berjalan, dan trigger terima apa adanya (#97) ikut menjaga. Penolakan tidak
boleh meninggalkan percobaan yang sudah terbuka.

Session ORM diikat ke koneksi tes dengan savepoint, jadi commit di repository
hanya menutup savepoint dan semuanya dibatalkan di akhir test.

Label: Positif = dibuka untuk dicoba ulang, Negatif = ditolak tanpa sisa.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.runs import session_retry
from app.modules.runs.schemas import SessionRetryRequest
from app.shared.exceptions import ConflictError
from app.shared.security import Role
from tests.measurement_pg import buat_admin, buat_item_snapshot, buat_produk, buat_sesi, ubah_status

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

JAM = datetime(2026, 11, 5, 4, 0, tzinfo=UTC)


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
def db(conn):
    """Session ORM yang commit-nya hanya menutup savepoint di dalam transaksi tes."""
    with Session(bind=conn, join_transaction_mode="create_savepoint") as sesi:
        yield sesi


def _sesi_otomatis(conn, admin_id: uuid.UUID, status: str) -> uuid.UUID:
    sesi_id = buat_sesi(
        conn, buat_produk(conn, admin_id), admin_id, session_type="automatic", repetitions=2
    )
    ubah_status(conn, sesi_id, status)
    return sesi_id


def _percobaan(conn, sesi_id: uuid.UUID, item_id: uuid.UUID, nomor: int, status: str):
    return conn.execute(
        text(
            "INSERT INTO attempts (id, session_id, snapshot_item_id, repetition_no, source, status, "
            "answer_text) VALUES (:id, :sesi, :item, :nomor, 'automatic', "
            "CAST(:status AS attempt_status_enum), :jawaban) RETURNING id"
        ),
        {
            "id": uuid.uuid4(),
            "sesi": sesi_id,
            "item": item_id,
            "nomor": nomor,
            "status": status,
            "jawaban": "Pasal 151 UU Ketenagakerjaan." if status == "succeeded" else None,
        },
    ).scalar_one()


@pytest.fixture
def siap(conn):
    """Sesi otomatis finished_with_failures: satu kasus, (gagal, berhasil)."""
    admin_id = buat_admin(conn)
    sesi_id = _sesi_otomatis(conn, admin_id, "running")
    item_id = buat_item_snapshot(conn, sesi_id, admin_id)
    gagal = _percobaan(conn, sesi_id, item_id, 1, "technical_failure")
    berhasil = _percobaan(conn, sesi_id, item_id, 2, "succeeded")
    ubah_status(conn, sesi_id, "finished_with_failures")
    return admin_id, sesi_id, gagal, berhasil


def _ulang(db, sesi_id: uuid.UUID, admin_id: uuid.UUID):
    return session_retry.retry_failed_attempts(
        db,
        sesi_id,
        SessionRetryRequest(all_failed=True),
        actor_id=admin_id,
        actor_role=Role.ADMIN,
        now=JAM,
    )


def _status(conn, tabel: str, baris_id: uuid.UUID) -> str:
    return conn.execute(
        text(f"SELECT status::text FROM {tabel} WHERE id = :id"), {"id": baris_id}
    ).scalar_one()


def test_percobaan_gagal_dibuka_lagi_dan_jawaban_berhasil_tetap(conn, db, siap):
    """Positif: trigger #89 membolehkan gagal -> pending; jawaban yang berhasil tidak berubah."""
    # Arrange
    admin_id, sesi_id, gagal, berhasil = siap

    # Act
    hasil = _ulang(db, sesi_id, admin_id)

    # Assert
    assert hasil.retried_attempt_ids == [gagal]
    assert _status(conn, "measurement_sessions", sesi_id) == "running"
    assert _status(conn, "attempts", gagal) == "pending"
    assert _status(conn, "attempts", berhasil) == "succeeded"
    assert (
        conn.execute(
            text("SELECT answer_text FROM attempts WHERE id = :id"), {"id": berhasil}
        ).scalar_one()
        == "Pasal 151 UU Ketenagakerjaan."
    )


def test_index_satu_sesi_running_menolak_tanpa_sisa(conn, db, siap):
    """Negatif: sesi otomatis lain running; 409 dan percobaan gagal tetap technical_failure."""
    # Arrange
    admin_id, sesi_id, gagal, _ = siap
    _sesi_otomatis(conn, admin_id, "running")

    # Act + Assert
    with pytest.raises(ConflictError) as galat:
        _ulang(db, sesi_id, admin_id)
    assert galat.value.code == "ANOTHER_SESSION_RUNNING"
    assert _status(conn, "measurement_sessions", sesi_id) == "finished_with_failures"
    assert _status(conn, "attempts", gagal) == "technical_failure"


def test_sesi_yang_diterima_apa_adanya_ditolak(conn, db, siap):
    """Negatif: AC9 #97, tanda terima apa adanya harus dihapus dulu; tidak ada yang terbuka."""
    # Arrange
    admin_id, sesi_id, gagal, _ = siap
    conn.execute(
        text(
            "UPDATE measurement_sessions SET accepted_as_is_at = :jam, accepted_as_is_by = :admin, "
            "accepted_as_is_reason = 'Kegagalan dari penyedia' WHERE id = :id"
        ),
        {"jam": JAM, "admin": admin_id, "id": sesi_id},
    )

    # Act + Assert
    with pytest.raises(ConflictError) as galat:
        _ulang(db, sesi_id, admin_id)
    assert galat.value.code == "SESSION_ACCEPTED"
    assert _status(conn, "attempts", gagal) == "technical_failure"
