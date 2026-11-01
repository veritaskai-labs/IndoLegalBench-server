"""Ganti jenis produk bersamaan dengan pembuatan sesi, di PostgreSQL. PBI-23 AC4, SCRUM-204.

Jira SCRUM-204 (edge): "ganti jenis dan membuat sesi pengukuran bersamaan → salah satunya
ditolak, tidak ada produk berjenis campur". Jira SCRUM-200 meminta cek riwayat dan update
dikunci (row lock) supaya tidak balapan dengan sesi yang baru dibuat.

Pembuatan sesi di database belum ada di staging (SCRUM-205/207), jadi pembuat sesi
diperankan koneksi kedua dengan SQL. Dua cara yang diuji:
- pembuat sesi yang mengunci baris produk (FOR SHARE) lalu mengecek jenis setelah kunci
  didapat: aman dengan kode sekarang (kontrak untuk SCRUM-205/207);
- pembuat sesi yang hanya mengandalkan foreign key: sesi yang belum di-commit tidak
  terlihat oleh cek riwayat maupun trigger, dan update_product tidak mengunci baris
  produk, jadi keduanya lolos dan produk berjenis campur.

SQLite mengabaikan kunci baris, jadi tes ini harus di PostgreSQL. Tidak ada jeda waktu:
urutan langkah diatur dengan threading.Event, dan "sedang menunggu kunci" dibaca dari
pg_stat_activity. lock_timeout 5 detik menjaga tes yang salah agar tidak menggantung.
"""

import os
import threading
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.providers import repository, service
from app.modules.providers.models import MeasurementType
from app.modules.providers.schemas import AiProductUpdate
from app.shared.config import get_settings
from app.shared.exceptions import ValidationError
from tests.measurement_type_qa_pg import (
    daftar_otomatis,
    jenis_dan_sesi,
    pengguna,
    tabel_sesi,
    tambah_sesi,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

KE_MANUAL = AiProductUpdate(
    measurement_type=MeasurementType.MANUAL, vendor_name="Penyedia", site_url="https://x.test"
)
BATAS_TUNGGU = 10.0


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    mesin = create_engine(
        POSTGRES_URL, pool_size=5, connect_args={"options": "-c lock_timeout=5000"}
    )
    with tabel_sesi(mesin):
        yield mesin
    mesin.dispose()
    get_settings.cache_clear()


@pytest.fixture
def buat_sesi(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def produk(buat_sesi) -> tuple[uuid.UUID, uuid.UUID]:
    """(product_id, admin_id): satu produk Otomatis tanpa riwayat, sudah di-commit."""
    with buat_sesi() as db:
        admin = pengguna(db)
        return daftar_otomatis(db, admin), admin


def _pid(conn: Connection) -> int:
    return conn.scalar(text("SELECT pg_backend_pid()"))


def _tunggu_kunci(engine, pid: int) -> None:
    """Tunggu sampai backend pid benar-benar menunggu kunci baris (bukan sekadar lambat)."""
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


def _buat_sesi_dengan_kunci(conn: Connection, product_id, admin) -> bool:
    """Pembuat sesi yang mengikuti kontrak: kunci baris produk, cek jenis, baru INSERT.

    Mengembalikan True bila sesi dibuat, False bila ditolak karena jenisnya tidak cocok (C6).
    """
    jenis = conn.scalar(
        text("SELECT measurement_type FROM ai_products WHERE id = :id FOR SHARE"),
        {"id": product_id},
    )
    if str(jenis) != "automatic":
        conn.rollback()
        return False
    tambah_sesi(conn, product_id, admin)
    conn.commit()
    return True


def test_pembuat_sesi_yang_mengunci_produk_menunggu_ganti_jenis_lalu_menolak(
    engine, buat_sesi, produk, monkeypatch
):
    """Edge: ganti jenis sudah menulis UPDATE tapi belum commit, lalu sesi otomatis dibuat.
    Pembuat sesi yang memakai FOR SHARE menunggu, membaca jenis Manual setelah commit, dan
    menolak. Hasil: satu ditolak, tidak ada jenis campur."""
    # Arrange
    product_id, admin = produk
    sudah_ditulis, lanjut_commit = threading.Event(), threading.Event()
    simpan_asli = repository.save

    def simpan_tertahan(db, product):
        db.flush()  # UPDATE terkirim dan baris produk terkunci, belum commit
        sudah_ditulis.set()
        assert lanjut_commit.wait(BATAS_TUNGGU)
        return simpan_asli(db, product)

    monkeypatch.setattr(repository, "save", simpan_tertahan)
    hasil: dict[str, object] = {}

    def ganti_jenis():
        with buat_sesi() as db:
            hasil["ganti"] = service.update_product(db, product_id, KE_MANUAL).measurement_type

    def buat_sesi_baru(conn):
        hasil["sesi"] = _buat_sesi_dengan_kunci(conn, product_id, admin)

    # Act
    penggati = threading.Thread(target=ganti_jenis)
    penggati.start()
    assert sudah_ditulis.wait(BATAS_TUNGGU)
    with engine.connect() as conn:
        pembuat = threading.Thread(target=buat_sesi_baru, args=(conn,))
        pid = _pid(conn)
        conn.rollback()
        pembuat.start()
        _tunggu_kunci(engine, pid)
        lanjut_commit.set()
        penggati.join(BATAS_TUNGGU)
        pembuat.join(BATAS_TUNGGU)

    # Assert
    assert hasil == {"ganti": MeasurementType.MANUAL, "sesi": False}
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("manual", [])


def test_sesi_baru_menunggu_selama_ganti_jenis_belum_commit(engine, buat_sesi, produk, monkeypatch):
    """Edge: selama ganti jenis belum commit, INSERT sesi untuk produk itu menunggu, juga
    bila pembuat sesi hanya mengandalkan foreign key. Kunci FOR UPDATE di update_product
    menahan kunci FK, jadi tidak ada sesi yang masuk di antara cek riwayat dan commit.

    Setelah ganti jenis commit, pembuat sesi tetap wajib membaca ulang jenis produk
    (FOR SHARE, lihat tes pertama) sebelum menyimpan; di sini sesinya dibatalkan."""
    # Arrange
    product_id, admin = produk
    sudah_dikunci, lanjut_commit = threading.Event(), threading.Event()
    simpan_asli = repository.save

    def simpan_tertahan(db, product):
        sudah_dikunci.set()
        assert lanjut_commit.wait(BATAS_TUNGGU)
        return simpan_asli(db, product)

    monkeypatch.setattr(repository, "save", simpan_tertahan)

    def ganti_jenis():
        with buat_sesi() as db:
            service.update_product(db, product_id, KE_MANUAL)

    def sisip_sesi(conn):
        tambah_sesi(conn, product_id, admin)
        conn.rollback()

    # Act
    penggati = threading.Thread(target=ganti_jenis)
    penggati.start()
    assert sudah_dikunci.wait(BATAS_TUNGGU)
    with engine.connect() as conn:
        pid = _pid(conn)
        conn.rollback()
        penyisip = threading.Thread(target=sisip_sesi, args=(conn,))
        penyisip.start()
        _tunggu_kunci(engine, pid)
        lanjut_commit.set()
        penggati.join(BATAS_TUNGGU)
        penyisip.join(BATAS_TUNGGU)

    # Assert
    assert not penggati.is_alive() and not penyisip.is_alive()
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("manual", [])


def test_ganti_jenis_menunggu_pembuat_sesi_yang_mengunci_lalu_ditolak(engine, buat_sesi, produk):
    """Edge: urutan sebaliknya. Pembuat sesi sudah memegang FOR SHARE dan INSERT, belum
    commit. UPDATE ganti jenis menunggu kunci; setelah sesi di-commit, trigger melihat
    sesi itu dan ganti jenis ditolak 422. Hasil: satu ditolak, tidak ada jenis campur."""
    # Arrange
    product_id, admin = produk
    hasil: dict[str, object] = {}

    def ganti_jenis(pid_tersimpan: dict):
        with buat_sesi() as db:
            pid_tersimpan["pid"] = db.connection().scalar(text("SELECT pg_backend_pid()"))
            siap.set()
            try:
                service.update_product(db, product_id, KE_MANUAL)
                hasil["ganti"] = "berhasil"
            except ValidationError as galat:
                hasil["ganti"] = galat.code

    siap = threading.Event()
    pid_penggati: dict[str, int] = {}

    # Act
    with engine.connect() as conn:
        jenis = conn.scalar(
            text("SELECT measurement_type FROM ai_products WHERE id = :id FOR SHARE"),
            {"id": product_id},
        )
        assert str(jenis) == "automatic"
        tambah_sesi(conn, product_id, admin)
        penggati = threading.Thread(target=ganti_jenis, args=(pid_penggati,))
        penggati.start()
        assert siap.wait(BATAS_TUNGGU)
        _tunggu_kunci(engine, pid_penggati["pid"])
        conn.commit()
        penggati.join(BATAS_TUNGGU)

    # Assert
    assert hasil == {"ganti": "MEASUREMENT_TYPE_IMMUTABLE"}
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("automatic", ["automatic"])


def test_sesi_belum_commit_tanpa_kunci_produk_tidak_boleh_menghasilkan_jenis_campur(
    engine, buat_sesi, produk
):
    """Negatif: pembuat sesi hanya mengandalkan foreign key (tanpa FOR SHARE) dan belum
    commit saat Admin mengganti jenis. Yang diharapkan Jira: salah satunya ditolak.

    FK hanya mengambil FOR KEY SHARE, yang tidak bentrok dengan kunci UPDATE kolom biasa.
    Tanpa kunci produk di update_product, cek riwayat dan trigger tidak melihat sesi yang
    belum di-commit, keduanya berhasil, dan produk Manual punya sesi Otomatis. Dengan
    FOR UPDATE sebelum cek riwayat, ganti jenis menunggu sesi di-commit lalu ditolak 422."""
    # Arrange
    product_id, admin = produk
    hasil: dict[str, object] = {}

    def ganti_jenis():
        with buat_sesi() as db:
            try:
                service.update_product(db, product_id, KE_MANUAL)
                hasil["ganti"] = "berhasil"
            except ValidationError as galat:
                hasil["ganti"] = galat.code

    # Act
    with engine.connect() as conn:
        tambah_sesi(conn, product_id, admin)
        pid = _pid(conn)
        penggati = threading.Thread(target=ganti_jenis)
        penggati.start()
        penggati.join(1.0)
        if penggati.is_alive():  # perilaku setelah diperbaiki: menunggu kunci sesi ini
            _tunggu_kunci(engine, _pid_selain(engine, pid))
        conn.commit()
        penggati.join(BATAS_TUNGGU)

    # Assert
    with engine.connect() as conn:
        jenis, sesi = jenis_dan_sesi(conn, product_id)
    assert all(item == jenis for item in sesi), (jenis, sesi, hasil)
    assert hasil == {"ganti": "MEASUREMENT_TYPE_IMMUTABLE"}


def _pid_selain(engine, pid_sendiri: int) -> int:
    """Backend lain yang sedang menunggu kunci di database ini."""
    with engine.connect() as pantau:
        return pantau.scalar(
            text(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' AND pid <> :pid LIMIT 1"
            ),
            {"pid": pid_sendiri},
        )
