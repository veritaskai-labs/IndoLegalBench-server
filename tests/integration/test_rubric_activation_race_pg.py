"""SCRUM-222: aktivasi versi rubrik dan penambahan isinya tidak boleh saling menyalip.

Review Kak Aileen di #111 (blocking 1) dan Cursor: rubric_content_guard dulu mengunci
baris versi hanya kalau locked_at sudah terisi. Selama aktivasi di transaksi lain belum
di-commit, baris itu masih terlihat belum terkunci, jadi INSERT isi tidak menunggu dan
masuk ke versi yang sesaat kemudian aktif. Arah sebaliknya: aktivasi menghitung aspek
tanpa menunggu INSERT yang belum di-commit.

Dua koneksi nyata dipakai, tanpa thread: lock_timeout membuat koneksi kedua gagal
dengan lock_not_available (55P03) kalau ia memang harus menunggu kunci koneksi pertama.
Data harus di-commit supaya terlihat lintas koneksi. Rubrik tidak bisa dihapus (trigger),
jadi fixture membersihkannya dengan session_replication_role = replica, yang mematikan
trigger untuk koneksi itu saja (butuh superuser, seperti container tes dan CI).
Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.grading_pg import aktifkan, buat_aspek, buat_level, buat_versi
from tests.measurement_pg import CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, buat_admin

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
LOCK_NOT_AVAILABLE = "55P03"
# Nomor versi jauh di atas data tes lain, supaya tidak bentrok dengan UNIQUE version_no.
NOMOR_VERSI = 9_222

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


@pytest.fixture
def mesin():
    engine = create_engine(POSTGRES_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def versi(mesin):
    """Versi ter-commit dengan satu aspek berbobot 100 dan satu level, belum aktif."""
    with mesin.begin() as conn:
        admin_id = buat_admin(conn)
        versi_id = buat_versi(conn, admin_id, version_no=NOMOR_VERSI)
        buat_aspek(conn, versi_id, "Dimensi A", 1, Decimal("100"))
        buat_level(conn, versi_id, 5)
    yield versi_id
    with mesin.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        for tabel in ("rubric_aspects", "rubric_levels"):
            conn.execute(
                text(f"DELETE FROM {tabel} WHERE rubric_version_id = :id"), {"id": versi_id}
            )
        conn.execute(text("DELETE FROM rubric_versions WHERE id = :id"), {"id": versi_id})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": admin_id})


def _sqlstate(aksi) -> str | None:
    try:
        aksi()
    except DBAPIError as exc:
        return exc.orig.sqlstate
    return None


def _tunggu_sebentar(conn) -> None:
    conn.execute(text("SET LOCAL lock_timeout = '300ms'"))


def test_isi_baru_menunggu_aktivasi_yang_belum_commit_lalu_ditolak(mesin, versi):
    """Aktivasi duluan: INSERT aspek harus menunggu kunci, lalu ditolak setelah commit."""
    with mesin.connect() as pengaktif, mesin.connect() as penambah:
        tx_aktif = pengaktif.begin()
        aktifkan(pengaktif, versi)

        tx_tambah = penambah.begin()
        _tunggu_sebentar(penambah)
        selagi_aktivasi = _sqlstate(lambda: buat_aspek(penambah, versi, "Dimensi B", 2))
        tx_tambah.rollback()

        tx_aktif.commit()
        with penambah.begin():
            setelah_aktivasi = _sqlstate(lambda: buat_aspek(penambah, versi, "Dimensi B", 2))

    assert selagi_aktivasi == LOCK_NOT_AVAILABLE
    assert setelah_aktivasi == INSUFFICIENT_PRIVILEGE


def test_aktivasi_menunggu_isi_yang_belum_commit_lalu_ikut_menghitungnya(mesin, versi):
    """INSERT duluan: aktivasi harus menunggu, lalu melihat aspek baru tanpa bobot.

    Aspek pertama berbobot 100, aspek baru kosong: campuran itu harus ditolak, jadi
    aktivasi yang benar-benar menghitung aspek baru gagal dengan CHECK_VIOLATION.
    """
    with mesin.connect() as penambah, mesin.connect() as pengaktif:
        tx_tambah = penambah.begin()
        buat_aspek(penambah, versi, "Dimensi B", 2)

        tx_aktif = pengaktif.begin()
        _tunggu_sebentar(pengaktif)
        selagi_menambah = _sqlstate(lambda: aktifkan(pengaktif, versi))
        tx_aktif.rollback()

        tx_tambah.commit()
        with pengaktif.begin():
            setelah_menambah = _sqlstate(lambda: aktifkan(pengaktif, versi))

    assert selagi_menambah == LOCK_NOT_AVAILABLE
    assert setelah_menambah == CHECK_VIOLATION
