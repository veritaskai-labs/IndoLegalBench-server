"""Mulai bersamaan dengan ganti jenis produk, di PostgreSQL. SCRUM-76 SCRUM-207.

Kontrak dari #90 (SCRUM-204, tests/integration/test_measurement_type_race_qa_pg.py):
pembuat sesi mengunci baris produk FOR SHARE dan membaca ulang jenisnya sebelum
INSERT sesi. Di sini pembuat sesinya kode sungguhan SCRUM-207: StartSessionService
dengan ProductLockAdapter dan SessionQueueRepository, satu session database.
Hanya ringkasan (SCRUM-206) yang palsu, karena yang diuji kuncinya.

Urutan diatur dengan threading.Event dan pg_stat_activity, tanpa jeda waktu.
lock_timeout 5 detik menjaga tes yang salah agar tidak menggantung.

Label: Positif = sesi tersimpan, Edge = balapan dengan ganti jenis.
"""

import dataclasses
import os
import threading
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.providers import repository as providers_repository
from app.modules.providers import service as providers_service
from app.modules.providers.models import MeasurementType
from app.modules.providers.schemas import AiProductUpdate
from app.modules.runs import adapters, repository, schemas, service
from app.shared.config import get_settings
from app.shared.exceptions import ValidationError
from tests.measurement_type_qa_pg import daftar_otomatis, jenis_dan_sesi, pengguna, tabel_sesi

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

KE_MANUAL = AiProductUpdate(
    measurement_type=MeasurementType.MANUAL, vendor_name="Penyedia", site_url="https://x.test"
)
BATAS_TUNGGU = 10.0


class _RingkasanLolos:
    """SCRUM-206 sudah diuji sendiri; di sini ringkasan selalu lolos supaya kunci yang diuji."""

    def summarize(self, request):
        return None


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
def produk(buat_sesi) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(product_id, admin_id, suite_id): produk Otomatis tanpa riwayat dan satu suite, di-commit.

    Suite sungguhan karena measurement_session_suites punya FK ke suites di PostgreSQL.
    """
    with buat_sesi() as db:
        admin = pengguna(db)
        suite_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO suites (id, name, status, created_by) "
                "VALUES (:id, :nama, 'active', :admin)"
            ),
            {"id": suite_id, "nama": f"Suite 207 {suite_id.hex[:8]}", "admin": admin},
        )
        db.commit()
        return daftar_otomatis(db, admin), admin, suite_id


class _PenilaiLolos:
    """Tes ini menguji kunci produk; penilai hanya perlu lolos pemeriksaan (PBI-14)."""

    def lock_available(self, judge_provider_id: uuid.UUID) -> bool:
        return True


class _SesiTanpaPenilaiTersimpan(repository.SessionQueueRepository):
    """Sesi tes ini tidak menyimpan penilai, supaya tidak ada baris judge_providers yang
    tersisa (sesi PostgreSQL tidak bisa dihapus lagi dan baris itu menahan downgrade migrasi)."""

    def create_queued(self, new):
        return super().create_queued(dataclasses.replace(new, judge_provider_id=None))


def _mulai(db, produk: tuple[uuid.UUID, uuid.UUID, uuid.UUID]) -> schemas.StartResponse:
    """Tombol Mulai seperti di dependencies.get_start_service, satu session database."""
    start = service.StartSessionService(
        summary=_RingkasanLolos(),
        sessions=_SesiTanpaPenilaiTersimpan(db),
        product_lock=adapters.ProductLockAdapter(db),
        judge_lock=_PenilaiLolos(),
    )
    product_id, admin_id, suite_id = produk
    permintaan = schemas.StartRequest(
        suite_ids=[suite_id],
        product_id=product_id,
        label="Run PG",
        purpose="trial",
        judge_provider_id=uuid.uuid4(),
    )
    return start.start(permintaan, created_by=admin_id)


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


def test_mulai_tanpa_ganti_jenis_menyimpan_sesi(engine, buat_sesi, produk):
    """Positif: kunci FOR SHARE tidak menghalangi Mulai biasa; sesi tersimpan queued."""
    # Arrange
    product_id = produk[0]

    # Act
    with buat_sesi() as db:
        hasil = _mulai(db, produk)

    # Assert
    assert hasil.status == "queued"
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("automatic", ["automatic"])


def test_mulai_menunggu_ganti_jenis_lalu_menolak_tanpa_jenis_campur(
    engine, buat_sesi, produk, monkeypatch
):
    """Edge: ganti jenis sudah menulis UPDATE tapi belum commit, lalu Mulai ditekan.
    Mulai menunggu kunci, membaca jenis Manual setelah commit, dan menolak 422.
    Hasil: produk Manual tanpa sesi otomatis (D1 C6, SCRUM-204)."""
    # Arrange
    product_id = produk[0]
    sudah_ditulis, lanjut_commit = threading.Event(), threading.Event()
    simpan_asli = providers_repository.save

    def simpan_tertahan(db, product):
        db.flush()  # UPDATE terkirim dan baris produk terkunci, belum commit
        sudah_ditulis.set()
        assert lanjut_commit.wait(BATAS_TUNGGU)
        return simpan_asli(db, product)

    monkeypatch.setattr(providers_repository, "save", simpan_tertahan)
    hasil: dict[str, object] = {}

    def ganti_jenis():
        with buat_sesi() as db:
            hasil["ganti"] = providers_service.update_product(
                db, product_id, KE_MANUAL
            ).measurement_type

    def tekan_mulai(db):
        try:
            _mulai(db, produk)
            hasil["mulai"] = "dibuat"
        except ValidationError as galat:
            hasil["mulai"] = galat.code

    # Act
    pengganti = threading.Thread(target=ganti_jenis)
    pengganti.start()
    assert sudah_ditulis.wait(BATAS_TUNGGU)
    with buat_sesi() as db:
        pid = db.scalar(text("SELECT pg_backend_pid()"))
        pemulai = threading.Thread(target=tekan_mulai, args=(db,))
        pemulai.start()
        _tunggu_kunci(engine, pid)
        lanjut_commit.set()
        pengganti.join(BATAS_TUNGGU)
        pemulai.join(BATAS_TUNGGU)

    # Assert
    assert hasil == {"ganti": MeasurementType.MANUAL, "mulai": "PRODUCT_NOT_AUTOMATIC"}
    with engine.connect() as conn:
        assert jenis_dan_sesi(conn, product_id) == ("manual", [])
