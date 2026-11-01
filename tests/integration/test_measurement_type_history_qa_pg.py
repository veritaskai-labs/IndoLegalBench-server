"""Jenis produk terkunci setelah ada riwayat, di PostgreSQL. PBI-23 AC4, SCRUM-204.

Tes SCRUM-200 untuk riwayat berjalan di SQLite dengan tabel tiruan (id, product_id), jadi
trigger trg_ai_products_measurement_type_immutable (migrasi a1f8c7e3d092) belum pernah
diuji. Yang dibuktikan di sini:
- sesi apa pun mengunci jenis: gagal, selesai dengan kegagalan, belum jalan, diarsipkan,
  dan sesi pencatatan manual (D2: sesi manual ada di tabel yang sama);
- trigger menolak UPDATE SQL mentah, jadi kunci jenis tidak hanya ada di service.

Ganti jenis yang bersamaan dengan pembuatan sesi ada di test_measurement_type_race_qa_pg.py.

Tabel sesi: tests/measurement_type_qa_pg.py. Data di-commit karena service yang commit;
setiap tes memakai produk dan pengguna baru. Dilewati kalau TEST_POSTGRES_URL kosong.
"""

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.modules.providers import service
from app.modules.providers.models import MeasurementType
from app.modules.providers.schemas import AiProductUpdate
from app.shared.config import get_settings
from app.shared.exceptions import ValidationError
from tests.measurement_type_qa_pg import (
    daftar_manual,
    daftar_otomatis,
    jenis_dan_sesi,
    pengguna,
    tabel_sesi,
    tabel_sesi_ada,
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
# Pindah ke Manual lewat SQL mentah: semua isian Otomatis dikosongkan supaya C1 tidak
# menolak lebih dulu. Yang tersisa hanya pemeriksaan trigger.
SQL_KE_MANUAL = text(
    "UPDATE ai_products SET measurement_type = 'manual', vendor_name = 'Penyedia', "
    "site_url = 'https://x.test', provider_type = NULL, base_url = NULL, model_name = NULL, "
    "credential_encrypted = NULL, credential_hint = NULL, rate_limit_per_minute = NULL, "
    "monthly_budget_idr = NULL WHERE id = :id"
)


@pytest.fixture
def engine(monkeypatch):
    """Kunci enkripsi sementara, dan lock_timeout supaya tes yang salah tidak menggantung."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    mesin = create_engine(POSTGRES_URL, connect_args={"options": "-c lock_timeout=5000"})
    yield mesin
    mesin.dispose()
    get_settings.cache_clear()


@pytest.fixture
def db(engine):
    with sessionmaker(bind=engine)() as sesi:
        yield sesi


@pytest.fixture
def sesi_ada(engine, db):
    """Tabel sesi selama tes. Sesi ORM ditutup dulu, supaya transaksinya yang masih terbuka
    tidak menahan DROP TABLE tabel khusus tes."""
    with tabel_sesi(engine):
        yield
        db.close()


def test_tanpa_tabel_sesi_hanya_service_yang_menjaga_jenis(engine, db):
    """Edge: sebelum SCRUM-205 membuat measurement_sessions, trigger tidak memeriksa apa pun
    dan SQL mentah bisa mengganti jenis. Kondisi sementara yang disengaja (to_regclass di
    a1f8c7e3d092); tes ini dilewati begitu tabel asli ada."""
    if tabel_sesi_ada(engine):
        pytest.skip("measurement_sessions sudah ada, trigger aktif")
    # Arrange
    product_id = daftar_otomatis(db, pengguna(db))

    # Act
    with engine.begin() as conn:
        conn.execute(SQL_KE_MANUAL, {"id": product_id})

    # Assert
    db.expire_all()
    assert service.get_product(db, product_id).measurement_type == MeasurementType.MANUAL


@pytest.mark.parametrize(
    ("jenis_produk", "session_type", "status", "arsip"),
    [
        pytest.param("automatic", "automatic", "failed", False, id="sesi-gagal"),
        pytest.param(
            "automatic", "automatic", "finished_with_failures", False, id="selesai-dengan-gagal"
        ),
        pytest.param("automatic", "automatic", "queued", False, id="belum-jalan"),
        pytest.param("automatic", "automatic", "finished", True, id="diarsipkan"),
        pytest.param("manual", "manual", "finished", False, id="sesi-pencatatan-manual"),
    ],
)
def test_sesi_apa_pun_mengunci_jenis(
    engine, db, sesi_ada, jenis_produk, session_type, status, arsip
):
    """Negatif: produk dengan satu sesi, apa pun status sesinya, ditolak 422 saat jenisnya
    diganti, dan isian produk tidak berubah."""
    # Arrange
    admin = pengguna(db)
    if jenis_produk == "manual":
        product_id = daftar_manual(db, admin)
        ganti = AiProductUpdate(
            measurement_type=MeasurementType.AUTOMATIC,
            provider_type="openai_compatible",
            base_url="https://api.example.test/v1",
            model_name="model-uji",
            credential="rahasia-baru-204",  # pragma: allowlist secret
            rate_limit_per_minute=30,
            monthly_budget_idr="500000.00",
        )
    else:
        product_id = daftar_otomatis(db, admin)
        ganti = KE_MANUAL
    with engine.begin() as conn:
        tambah_sesi(conn, product_id, admin, session_type=session_type, status=status, arsip=arsip)

    # Act
    with pytest.raises(ValidationError) as galat:
        service.update_product(db, product_id, ganti)

    # Assert
    assert (galat.value.status_code, galat.value.code) == (422, "MEASUREMENT_TYPE_IMMUTABLE")
    db.rollback()
    produk = service.get_product(db, product_id)
    assert produk.measurement_type == jenis_produk
    assert produk.has_credential is (jenis_produk == "automatic")


def test_trigger_menolak_sql_mentah_pada_produk_berriwayat(engine, db, sesi_ada):
    """Negatif: UPDATE langsung ke database (tanpa service) juga ditolak, dengan pesan
    MEASUREMENT_TYPE_IMMUTABLE dan SQLSTATE check_violation."""
    # Arrange
    admin = pengguna(db)
    product_id = daftar_otomatis(db, admin)
    with engine.begin() as conn:
        tambah_sesi(conn, product_id, admin)

    # Act
    with pytest.raises(IntegrityError) as galat, engine.begin() as conn:
        conn.execute(SQL_KE_MANUAL, {"id": product_id})

    # Assert
    assert galat.value.orig.sqlstate == "23514"
    assert "MEASUREMENT_TYPE_IMMUTABLE" in str(galat.value.orig)
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("automatic", ["automatic"])


def test_trigger_mengizinkan_ganti_jenis_tanpa_riwayat(engine, db, sesi_ada):
    """Positif: trigger hanya memeriksa sesi milik produk itu. Sesi produk lain tidak
    mengunci, dan SQL mentah yang sama berhasil."""
    # Arrange
    admin = pengguna(db)
    product_id = daftar_otomatis(db, admin)
    lain = daftar_otomatis(db, admin)
    with engine.begin() as conn:
        tambah_sesi(conn, lain, admin)

    # Act
    with engine.begin() as conn:
        conn.execute(SQL_KE_MANUAL, {"id": product_id})

    # Assert
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("manual", [])
