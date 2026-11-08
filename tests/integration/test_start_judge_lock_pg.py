"""Mulai bersamaan dengan menonaktifkan penilai, di PostgreSQL. SCRUM-78 SCRUM-258.

Pembuat sesi mengunci baris penilai FOR SHARE dan membaca ulang is_active di
transaksi yang sama dengan INSERT sesi, seperti kunci produk #90. Kunci hanya
berdasarkan id, tidak menyaring is_active: nonaktifkan yang belum commit harus
ditunggu lalu terbaca (pelajaran SCRUM-222, catatan Aileen soal kunci yang
menyaring kolom yang diperiksa). Pembuat sesinya kode sungguhan: StartSessionService
dengan ProductLockAdapter, JudgeLockAdapter, dan SessionQueueRepository. Hanya
ringkasan yang palsu.

Urutan diatur dengan threading.Event dan pg_stat_activity, tanpa jeda waktu.
"""

import os
import threading
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.modules.providers import repository as providers_repository
from app.modules.providers import service as providers_service
from app.modules.runs import adapters, repository, schemas, service
from app.shared.config import get_settings
from app.shared.exceptions import ValidationError
from tests.integration.test_start_session_lock_pg import (
    BATAS_TUNGGU,
    _RingkasanLolos,
    _tunggu_kunci,
)
from tests.measurement_type_qa_pg import daftar_otomatis, pengguna, tabel_sesi

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


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
def siap(buat_sesi) -> dict[str, uuid.UUID]:
    """Produk Otomatis, satu suite, dan satu penilai (produk otomatis lain), di-commit.

    Sesi dengan penilai sungguhan (FK) sudah diuji di test_judge_providers_pg.py di dalam
    transaksi yang di-rollback, jadi di sini tidak ada sesi yang tersimpan.
    """
    with buat_sesi() as db:
        admin = pengguna(db)
        suite_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO suites (id, name, status, created_by) "
                "VALUES (:id, :nama, 'active', :admin)"
            ),
            {"id": suite_id, "nama": f"Suite 258 {suite_id.hex[:8]}", "admin": admin},
        )
        db.commit()
        isi = {
            "produk": daftar_otomatis(db, admin),
            "admin": admin,
            "suite": suite_id,
            "penilai": daftar_otomatis(db, admin),
        }
    return isi


def _mulai(db, siap) -> schemas.StartResponse:
    """Tombol Mulai seperti dependencies.get_start_service, satu session database."""
    start = service.StartSessionService(
        summary=_RingkasanLolos(),
        sessions=repository.SessionQueueRepository(db),
        product_lock=adapters.ProductLockAdapter(db),
        judge_lock=adapters.JudgeLockAdapter(db),
    )
    permintaan = schemas.StartRequest(
        suite_ids=[siap["suite"]],
        product_id=siap["produk"],
        label="Run PG 258",
        purpose="trial",
        judge_provider_id=siap["penilai"],
    )
    return start.start(permintaan, created_by=siap["admin"])


def test_mulai_menunggu_nonaktifkan_penilai_lalu_menolak(engine, buat_sesi, siap, monkeypatch):
    """Edge: nonaktifkan sudah menulis UPDATE tapi belum commit, lalu Mulai ditekan.
    Mulai menunggu kunci baris penilai, membaca is_active = false setelah commit, dan
    menolak 422. Tanpa kunci, sesi tersimpan dengan penilai yang sudah nonaktif."""
    sudah_ditulis, lanjut_commit = threading.Event(), threading.Event()
    simpan_asli = providers_repository.save

    def simpan_tertahan(db, judge):
        db.flush()  # UPDATE terkirim dan baris penilai terkunci, belum commit
        sudah_ditulis.set()
        assert lanjut_commit.wait(BATAS_TUNGGU)
        return simpan_asli(db, judge)

    monkeypatch.setattr(providers_repository, "save", simpan_tertahan)
    hasil: dict[str, object] = {}

    def nonaktifkan():
        with buat_sesi() as db:
            hasil["nonaktif"] = providers_service.set_active(
                db, siap["penilai"], is_active=False
            ).is_active

    def tekan_mulai(db):
        try:
            _mulai(db, siap)
            hasil["mulai"] = "dibuat"
        except ValidationError as galat:
            hasil["mulai"] = galat.code

    pengubah = threading.Thread(target=nonaktifkan)
    pengubah.start()
    assert sudah_ditulis.wait(BATAS_TUNGGU)
    with buat_sesi() as db:
        pid = db.scalar(text("SELECT pg_backend_pid()"))
        pemulai = threading.Thread(target=tekan_mulai, args=(db,))
        pemulai.start()
        _tunggu_kunci(engine, pid)
        lanjut_commit.set()
        pengubah.join(BATAS_TUNGGU)
        pemulai.join(BATAS_TUNGGU)

    assert hasil == {"nonaktif": False, "mulai": "JUDGE_PROVIDER_NOT_AVAILABLE"}
    with engine.connect() as conn:
        jumlah = conn.scalar(
            text("SELECT count(*) FROM measurement_sessions WHERE judge_provider_id = :id"),
            {"id": siap["penilai"]},
        )
    assert jumlah == 0
