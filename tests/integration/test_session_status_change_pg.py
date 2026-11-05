"""Perpindahan status sesi di PostgreSQL. PBI-12 AC9, AC10, SCRUM-214.

Dua hal yang SQLite tidak bisa buktikan:
- CHECK stopped_at dan stop_reason harus lengkap berdua atau kosong berdua.
- UPDATE bersyarat change_status aman dari balapan: penulis kedua yang menunggu kunci
  baris membaca ulang status setelah penulis pertama commit, dan ditolak bila asalnya
  tidak lagi sah. Baca-lalu-tulis biasa akan menimpa status akhir yang sudah ada.

Data balapan harus di-commit, dan sesi tidak bisa dihapus (trigger SCRUM-205), jadi
sesinya manual (tidak memakai slot running otomatis) dan berakhir di status akhir
(bukan paused, yang membuat tes downgrade migrasi menolak). Urutan diatur dengan
pg_stat_activity, tanpa jeda waktu; lock_timeout 5 detik menjaga tes agar tidak menggantung.

Label: Positif = tersimpan, Negatif = ditolak, Edge = balapan.
"""

import os
import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.runs import repository
from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError
from tests.measurement_pg import (
    CHECK_VIOLATION,
    buat_admin,
    buat_produk,
    buat_sesi,
    galat,
    ubah_status,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
JAM = datetime(2026, 11, 4, 7, 0, tzinfo=UTC)
BATAS_TUNGGU = 10.0

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def engine():
    mesin = create_engine(POSTGRES_URL, connect_args={"options": "-c lock_timeout=5000"})
    yield mesin
    mesin.dispose()


@pytest.fixture
def conn(engine):
    with engine.connect() as koneksi:
        transaksi = koneksi.begin()
        try:
            yield koneksi
        finally:
            transaksi.rollback()


@pytest.mark.parametrize(
    "isian",
    [
        "stopped_at = now()",
        "stop_reason = 'budget'",
    ],
)
def test_penyebab_berhenti_harus_lengkap(conn, isian):
    """Negatif: waktu tanpa penyebab, atau penyebab tanpa waktu, ditolak CHECK 23514."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)

    # Act
    kesalahan = galat(
        conn,
        lambda: conn.execute(
            text(f"UPDATE measurement_sessions SET {isian} WHERE id = :id"), {"id": sesi_id}
        ),
    )

    # Assert
    assert kesalahan.sqlstate == CHECK_VIOLATION


def test_penyebab_berhenti_lengkap_tersimpan(conn):
    """Positif: waktu dan penyebab bersama-sama lolos."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)

    # Act
    conn.execute(
        text(
            "UPDATE measurement_sessions SET stopped_at = now(), stop_reason = 'restart' "
            "WHERE id = :id"
        ),
        {"id": sesi_id},
    )

    # Assert
    assert (
        conn.scalar(
            text("SELECT stop_reason::text FROM measurement_sessions WHERE id = :id"),
            {"id": sesi_id},
        )
        == "restart"
    )


def _tunggu_kunci(engine, pid: int) -> None:
    batas = time.monotonic() + BATAS_TUNGGU
    with engine.connect() as pantau:
        while time.monotonic() < batas:
            jenis = pantau.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            pantau.rollback()
            if jenis == "Lock":
                return
            time.sleep(0.02)
    pytest.fail(f"backend {pid} tidak pernah menunggu kunci")


def test_penulis_kedua_kalah_dan_tidak_menimpa_status_akhir(engine):
    """Edge: worker menandai finished sementara Admin menandai failed; yang kedua ditolak 409."""
    # Arrange: sesi manual running, ter-commit.
    with engine.begin() as siapkan:
        admin_id = buat_admin(siapkan)
        sesi_id = buat_sesi(siapkan, buat_produk(siapkan, admin_id), admin_id)
        siapkan.execute(
            text("UPDATE measurement_sessions SET status = 'running' WHERE id = :id"),
            {"id": sesi_id},
        )

    hasil: dict[str, object] = {}
    pid_siap = threading.Event()

    def penulis_kedua() -> None:
        with sessionmaker(bind=engine)() as db:
            hasil["pid"] = db.scalar(text("SELECT pg_backend_pid()"))
            db.commit()
            pid_siap.set()
            try:
                repository.SessionStatusRepository(db).change_status(
                    sesi_id, SessionStatus.FAILED, at=JAM, stop_reason="error"
                )
                hasil["galat"] = None
            except ConflictError as exc:
                hasil["galat"] = exc

    # Act: penulis pertama memegang kunci baris, penulis kedua menunggu, lalu yang pertama commit.
    with engine.connect() as pertama:
        transaksi = pertama.begin()
        pertama.execute(
            text("SELECT 1 FROM measurement_sessions WHERE id = :id FOR UPDATE"), {"id": sesi_id}
        )
        benang = threading.Thread(target=penulis_kedua)
        benang.start()
        assert pid_siap.wait(BATAS_TUNGGU)
        _tunggu_kunci(engine, hasil["pid"])
        pertama.execute(
            text(
                "UPDATE measurement_sessions SET status = 'finished', finished_at = now() "
                "WHERE id = :id"
            ),
            {"id": sesi_id},
        )
        transaksi.commit()
    benang.join(BATAS_TUNGGU)

    # Assert
    assert isinstance(hasil["galat"], ConflictError)
    assert hasil["galat"].details == {"from": "finished", "to": "failed"}
    with engine.connect() as cek:
        assert (
            cek.scalar(
                text("SELECT status::text FROM measurement_sessions WHERE id = :id"),
                {"id": sesi_id},
            )
            == "finished"
        )


_TERIMA = (
    "UPDATE measurement_sessions SET accepted_as_is_at = now(), accepted_as_is_by = :admin_id, "
    "accepted_as_is_reason = 'Kegagalan dari penyedia' WHERE id = :id"
)


@pytest.mark.parametrize("status", ["queued", "running", "finished", "failed"])
def test_terima_apa_adanya_hanya_untuk_selesai_dengan_kegagalan(conn, status):
    """Negatif (catatan Adra #89): database menolak tanda terima pada status lain, 23514."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, status)

    # Act
    kesalahan = galat(
        conn, lambda: conn.execute(text(_TERIMA), {"id": sesi_id, "admin_id": admin_id})
    )

    # Assert
    assert kesalahan.sqlstate == CHECK_VIOLATION


def test_tanda_terima_boleh_dihapus(conn):
    """Positif (keputusan Rafa 4 Nov): Admin boleh menghapus tanda; ketiga kolom kosong lagi."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, "finished_with_failures")
    conn.execute(text(_TERIMA), {"id": sesi_id, "admin_id": admin_id})

    # Act
    conn.execute(
        text(
            "UPDATE measurement_sessions SET accepted_as_is_at = NULL, accepted_as_is_by = NULL, "
            "accepted_as_is_reason = NULL WHERE id = :id"
        ),
        {"id": sesi_id},
    )

    # Assert
    assert (
        conn.scalar(
            text("SELECT accepted_as_is_at FROM measurement_sessions WHERE id = :id"),
            {"id": sesi_id},
        )
        is None
    )


def test_sesi_yang_diterima_tidak_bisa_kembali_berjalan(conn):
    """Negatif: coba ulang lewat SQL langsung juga ditolak selama tanda terima ada, 23514."""
    # Arrange
    admin_id = buat_admin(conn)
    sesi_id = buat_sesi(conn, buat_produk(conn, admin_id), admin_id)
    ubah_status(conn, sesi_id, "finished_with_failures")
    conn.execute(text(_TERIMA), {"id": sesi_id, "admin_id": admin_id})

    # Act
    kesalahan = galat(conn, lambda: ubah_status(conn, sesi_id, "running"))

    # Assert
    assert kesalahan.sqlstate == CHECK_VIOLATION
