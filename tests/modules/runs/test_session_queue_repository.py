"""Antrean sesi di tabel measurement_sessions. SCRUM-76 SCRUM-207.

PBI-11 AC1 dan AC3 di atas tabel SCRUM-205 (#89, D2): tombol Mulai menyimpan
sesi otomatis berstatus queued dengan queued_at, beserta suite pilihannya di
measurement_session_suites. Antrean FIFO menurut queued_at; sesi yang berjalan,
selesai, diarsipkan, atau manual tidak ikut antre. SessionQueueRepository
memenuhi port SessionQueueStore, jadi service dan router tidak berubah.

Database uji SQLite: FK tidak ditegakkan, jadi produk dan suite cukup id acak.
Partial unique index "satu sesi otomatis running" milik #89 tetap aktif.

Label: Positif = tersimpan atau terbaca benar, Negatif = tidak ikut antre,
Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.runs import repository, schemas, session_ports
from app.modules.runs.models import (
    MeasurementSession,
    MeasurementSessionSuite,
    SessionStatus,
    SessionType,
)

PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d4")
SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
JAM = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)


def _baru(label: str = "Run November", **ubah) -> session_ports.NewSession:
    nilai = {
        "label": label,
        "purpose": schemas.MeasurementPurpose.TRIAL,
        "product_id": PRODUCT_ID,
        "suite_ids": (SUITE_A, SUITE_B),
        "case_filter": schemas.CaseFilter.DEV,
        "repetitions": 4,
        "created_by": ADMIN_ID,
    }
    nilai.update(ubah)
    return session_ports.NewSession(**nilai)


def _jam(*daftar: datetime):
    """Jam palsu yang maju sesuai daftar, supaya urutan queued_at pasti."""
    berikut = iter(daftar)
    return lambda: next(berikut)


@pytest.fixture
def antrean(db_session):
    """Repository dengan jam tetap; test yang butuh jam lain membuat sendiri."""
    return repository.SessionQueueRepository(db_session, clock=lambda: JAM)


def test_mulai_menyimpan_sesi_otomatis_menunggu_dengan_semua_pilihan(antrean, db_session):
    """Positif: AC1, AC3, baris sesi memuat pilihan pemulai, status queued, dan queued_at."""
    # Act
    hasil = antrean.create_queued(_baru())

    # Assert
    baris = db_session.get(MeasurementSession, hasil.id)
    assert hasil == session_ports.QueuedSession(
        id=baris.id, status=SessionStatus.QUEUED, queued_at=JAM
    )
    assert baris.label == "Run November"
    assert baris.purpose.value == "trial"
    assert baris.session_type == SessionType.AUTOMATIC
    assert baris.product_id == PRODUCT_ID
    assert baris.split_filter.value == "dev"
    assert baris.repetitions == 4
    assert baris.created_by == ADMIN_ID
    assert baris.status == SessionStatus.QUEUED
    assert baris.started_at is None


def test_suite_pilihan_tersimpan_per_sesi(antrean, db_session):
    """Positif: AC2, kasus dipilih per suite, satu baris per suite di measurement_session_suites."""
    # Act
    hasil = antrean.create_queued(_baru())

    # Assert
    suites = db_session.scalars(
        select(MeasurementSessionSuite.suite_id).where(
            MeasurementSessionSuite.session_id == hasil.id
        )
    ).all()
    assert set(suites) == {SUITE_A, SUITE_B}


def test_sesi_tertua_dibaca_kembali_dengan_pilihannya(antrean):
    """Positif: worker mendapat pilihan yang sama dengan yang dikirim saat Mulai."""
    # Arrange
    hasil = antrean.create_queued(_baru())

    # Act
    tertua = antrean.oldest_queued()

    # Assert
    assert tertua.id == hasil.id
    assert tertua.new == _baru(suite_ids=tuple(sorted((SUITE_A, SUITE_B))))


def test_yang_masuk_lebih_dulu_jalan_lebih_dulu(db_session):
    """Positif: AC3, FIFO menurut queued_at (D2), bukan urutan id."""
    # Arrange
    antrean = repository.SessionQueueRepository(
        db_session, clock=_jam(JAM, JAM + timedelta(minutes=5))
    )
    pertama = antrean.create_queued(_baru("Pertama"))
    antrean.create_queued(_baru("Kedua"))

    # Act + Assert
    assert antrean.oldest_queued().id == pertama.id


@pytest.mark.parametrize(
    "ubah",
    [
        # Negatif: sudah berjalan atau sudah selesai, bukan antrean lagi.
        {"status": SessionStatus.RUNNING},
        {"status": SessionStatus.FINISHED},
        # Negatif: sesi manual (PBI-22) tidak dijalankan worker otomatis.
        {"session_type": SessionType.MANUAL},
        # Negatif: sesi yang diarsipkan disembunyikan (AC10), tidak boleh diam-diam jalan.
        {"archived_at": JAM, "archived_by": ADMIN_ID},
    ],
)
def test_sesi_yang_bukan_antrean_otomatis_tidak_diambil(antrean, db_session, ubah):
    """Negatif: hanya sesi otomatis yang masih queued dan tidak diarsipkan yang antre."""
    # Arrange
    hasil = antrean.create_queued(_baru())
    baris = db_session.get(MeasurementSession, hasil.id)
    for kolom, nilai in ubah.items():
        setattr(baris, kolom, nilai)
    db_session.commit()

    # Act + Assert
    assert antrean.oldest_queued() is None


def test_antrean_kosong_dan_tanpa_sesi_berjalan(antrean):
    """Edge: database tanpa sesi tidak punya sesi tertua maupun sesi berjalan."""
    # Act + Assert
    assert antrean.oldest_queued() is None
    assert antrean.running_exists() is False


def test_sesi_otomatis_berjalan_terdeteksi(antrean, db_session):
    """Positif: AC3, ada sesi otomatis running berarti antrean menunggu."""
    # Arrange
    hasil = antrean.create_queued(_baru())
    db_session.get(MeasurementSession, hasil.id).status = SessionStatus.RUNNING
    db_session.commit()

    # Act + Assert
    assert antrean.running_exists() is True


def test_sesi_manual_berjalan_tidak_menahan_antrean_otomatis(antrean, db_session):
    """Edge: batas satu running hanya untuk sesi otomatis (D2 partial index)."""
    # Arrange
    hasil = antrean.create_queued(_baru())
    baris = db_session.get(MeasurementSession, hasil.id)
    baris.session_type = SessionType.MANUAL
    baris.status = SessionStatus.RUNNING
    db_session.commit()

    # Act + Assert
    assert antrean.running_exists() is False


def test_tanpa_jam_suntikan_queued_at_memakai_utc_sekarang(db_session):
    """Edge: queued_at dari jam bawaan ber-zona UTC, supaya urutan antrean tidak tergeser WIB."""
    # Arrange
    antrean = repository.SessionQueueRepository(db_session)
    sebelum = datetime.now(UTC)

    # Act
    hasil = antrean.create_queued(_baru())

    # Assert
    assert hasil.queued_at.tzinfo is UTC
    assert hasil.queued_at >= sebelum
