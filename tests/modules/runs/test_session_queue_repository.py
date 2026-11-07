"""Antrean sesi di tabel measurement_sessions. SCRUM-76 SCRUM-207.

PBI-11 AC1 dan AC3 di atas tabel SCRUM-205 (#89, D2): tombol Mulai menyimpan
sesi otomatis berstatus queued dengan queued_at, beserta suite pilihannya di
measurement_session_suites. Antrean FIFO menurut queued_at; sesi yang berjalan,
selesai, diarsipkan, atau manual tidak ikut antre. SessionQueueRepository
memenuhi port SessionQueueStore, jadi service dan router tidak berubah.

Saat giliran tiba (AC2, AC6, D3 langkah 25-28), start() dalam satu transaksi:
status running, snapshot kasus, salinan produk tanpa kredensial, dan attempts
pending untuk setiap kasus x pengulangan. Bila sesi lain sudah running, partial
index menolak, semuanya dibatalkan, dan start() menjawab False. Galat lain tidak
disembunyikan. Perilaku trigger PostgreSQL diuji di
tests/integration/test_session_queue_repository_pg.py.

Database uji SQLite: FK tidak ditegakkan, jadi produk dan suite cukup id acak.
Partial unique index "satu sesi otomatis running" milik #89 tetap aktif.

Label: Positif = tersimpan atau terbaca benar, Negatif = tidak ikut antre,
Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.modules.runs import repository, schemas, session_ports
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    MeasurementSessionSuite,
    SessionProductCopy,
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


MULAI = JAM + timedelta(hours=1)


def _item(kode: str, tag: str = "dev") -> session_ports.SnapshotItem:
    return session_ports.SnapshotItem(
        case_id=uuid.uuid5(uuid.NAMESPACE_URL, kode),
        case_version_id=uuid.uuid5(uuid.NAMESPACE_DNS, kode),
        split_tag=tag,
        content={"version_no": 1, "sections": {"case_code": kode, "split_tag": tag}},
    )


ITEMS = [_item("PHK-001"), _item("PHK-002")]


def _salinan(**ubah) -> session_ports.ProductCopy:
    nilai = {
        "product_id": PRODUCT_ID,
        "name": "AiYU",
        "measurement_type": "automatic",
        "provider_type": "openai_compatible",
        "base_url": "https://api.example.test/v1/chat/completions",
        "model_name": "deepseek-flash",
        "model_params": {"temperature": 0.3},
        "price_input_usd_per_mtok": Decimal("1.0000"),
        "price_output_usd_per_mtok": Decimal("10.0000"),
        "monthly_budget_idr": Decimal("500000.00"),
    }
    nilai.update(ubah)
    return session_ports.ProductCopy(**nilai)


def _jumlah(db_session, model, session_id) -> int:
    return db_session.scalar(
        select(func.count()).select_from(model).where(model.session_id == session_id)
    )


def test_start_menjalankan_sesi_dengan_snapshot_salinan_dan_percobaan(antrean, db_session):
    """Positif: AC2, AC6, D3 langkah 25-28, semuanya tercatat untuk sesi ini."""
    # Arrange
    sesi = antrean.create_queued(_baru(repetitions=3))

    # Act
    berhasil = antrean.start(sesi.id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Assert
    assert berhasil is True
    baris = db_session.get(MeasurementSession, sesi.id)
    assert baris.status == SessionStatus.RUNNING
    assert baris.started_at.replace(tzinfo=UTC) == MULAI
    snapshot = db_session.scalars(
        select(CaseSnapshot).where(CaseSnapshot.session_id == sesi.id)
    ).one()
    items = db_session.scalars(
        select(CaseSnapshotItem).where(CaseSnapshotItem.snapshot_id == snapshot.id)
    ).all()
    assert {(i.case_id, i.case_version_id) for i in items} == {
        (item.case_id, item.case_version_id) for item in ITEMS
    }
    assert {i.body["sections"]["case_code"] for i in items} == {"PHK-001", "PHK-002"}


def test_salinan_produk_tersimpan_tanpa_kredensial(antrean, db_session):
    """Positif: AC6, data produk saat sesi berjalan disalin, kredensial tidak ada kolomnya."""
    # Arrange
    sesi = antrean.create_queued(_baru())

    # Act
    antrean.start(sesi.id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Assert
    salinan = db_session.scalars(
        select(SessionProductCopy).where(SessionProductCopy.session_id == sesi.id)
    ).one()
    assert (salinan.product_id, salinan.name, salinan.model_name) == (
        PRODUCT_ID,
        "AiYU",
        "deepseek-flash",
    )
    assert (salinan.measurement_type, salinan.provider_type) == ("automatic", "openai_compatible")
    assert salinan.base_url == "https://api.example.test/v1/chat/completions"
    assert salinan.model_params == {"temperature": 0.3}
    assert salinan.price_output_usd_per_mtok == Decimal("10.0000")
    assert salinan.monthly_budget_idr == Decimal("500000.00")
    assert salinan.model_version_at_start is None


def test_parameter_model_kosong_tersimpan_sebagai_null_bukan_json_null(antrean, db_session):
    """Edge: produk tanpa model_params memakai bawaan penyedia; kolomnya NULL, bukan 'null'."""
    # Arrange
    sesi = antrean.create_queued(_baru())

    # Act
    antrean.start(sesi.id, items=ITEMS, product=_salinan(model_params=None), started_at=MULAI)

    # Assert
    kosong = db_session.execute(
        text("SELECT model_params IS NULL FROM session_product_copies WHERE session_id = :id"),
        {"id": sesi.id.hex},
    ).scalar_one()
    assert kosong == 1


def test_percobaan_pending_untuk_setiap_kasus_dan_pengulangan(antrean, db_session):
    """Positif: D3 langkah 28, satu attempt per kasus x repetition_no 1..n, otomatis dan pending."""
    # Arrange
    sesi = antrean.create_queued(_baru(repetitions=3))

    # Act
    antrean.start(sesi.id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Assert
    percobaan = db_session.scalars(select(Attempt).where(Attempt.session_id == sesi.id)).all()
    assert len(percobaan) == 6
    per_item: dict[uuid.UUID, list[int]] = {}
    for satu in percobaan:
        per_item.setdefault(satu.snapshot_item_id, []).append(satu.repetition_no)
        assert satu.status == AttemptStatus.PENDING
        assert satu.source == AttemptSource.AUTOMATIC
        assert satu.answer_text is None
    assert sorted(sorted(nomor) for nomor in per_item.values()) == [[1, 2, 3], [1, 2, 3]]


def test_start_ditolak_saat_sesi_otomatis_lain_running_dan_tidak_meninggalkan_apa_pun(
    db_session,
):
    """Negatif: AC3, partial index menolak; sesi kedua tetap Menunggu tanpa snapshot sisa."""
    # Arrange
    antrean = repository.SessionQueueRepository(
        db_session, clock=_jam(JAM, JAM + timedelta(minutes=1))
    )
    pertama = antrean.create_queued(_baru("Pertama"))
    kedua = antrean.create_queued(_baru("Kedua"))
    antrean.start(pertama.id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Act
    berhasil = antrean.start(kedua.id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Assert
    assert berhasil is False
    assert db_session.get(MeasurementSession, kedua.id).status == SessionStatus.QUEUED
    for model in (CaseSnapshot, SessionProductCopy, Attempt):
        assert _jumlah(db_session, model, kedua.id) == 0


@pytest.mark.parametrize("keadaan", ["running", "arsip", "tidak_ada"])
def test_start_hanya_untuk_sesi_otomatis_yang_masih_menunggu(antrean, db_session, keadaan):
    """Edge: sesi yang sudah jalan, diarsipkan, atau tidak ada tidak dimulai lagi."""
    # Arrange
    sesi = antrean.create_queued(_baru())
    sesi_id = sesi.id
    if keadaan == "running":
        antrean.start(sesi_id, items=ITEMS, product=_salinan(), started_at=MULAI)
    elif keadaan == "arsip":
        baris = db_session.get(MeasurementSession, sesi_id)
        baris.archived_at, baris.archived_by = JAM, ADMIN_ID
        db_session.commit()
    else:
        sesi_id = uuid.uuid4()
    sebelum = _jumlah(db_session, Attempt, sesi_id)

    # Act
    berhasil = antrean.start(sesi_id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Assert
    assert berhasil is False
    assert _jumlah(db_session, Attempt, sesi_id) == sebelum


def test_repository_menerima_snapshot_kosong_tanpa_percobaan(antrean, db_session):
    """Edge: repository tidak menilai isi snapshot; snapshot kosong tetap satu transaksi utuh.

    SessionQueue yang memutuskan sesi tanpa kasus layak menjadi failed sebelum start.
    """
    # Arrange
    sesi = antrean.create_queued(_baru())

    # Act
    berhasil = antrean.start(sesi.id, items=[], product=_salinan(), started_at=MULAI)

    # Assert
    assert berhasil is True
    assert _jumlah(db_session, CaseSnapshot, sesi.id) == 1
    assert _jumlah(db_session, Attempt, sesi.id) == 0


def test_galat_data_lain_tidak_disembunyikan_dan_sesi_tetap_menunggu(antrean, db_session):
    """Negatif: kasus ganda di snapshot bukan soal antrean; galatnya naik, semuanya dibatalkan."""
    # Arrange
    sesi = antrean.create_queued(_baru())
    ganda = [ITEMS[0], ITEMS[0]]

    # Act
    with pytest.raises(IntegrityError):
        antrean.start(sesi.id, items=ganda, product=_salinan(), started_at=MULAI)

    # Assert
    assert db_session.get(MeasurementSession, sesi.id).status == SessionStatus.QUEUED
    assert _jumlah(db_session, CaseSnapshot, sesi.id) == 0


def test_gagal_menandai_sesi_failed_dan_mengeluarkannya_dari_antrean(antrean, db_session):
    """Positif: D4 failed untuk kesalahan fatal sebelum mulai; finished_at diisi, antrean lanjut."""
    # Arrange
    sesi = antrean.create_queued(_baru())

    # Act
    berhasil = antrean.fail(sesi.id, finished_at=MULAI)

    # Assert
    assert berhasil is True
    baris = db_session.get(MeasurementSession, sesi.id)
    assert baris.status == SessionStatus.FAILED
    assert baris.finished_at.replace(tzinfo=UTC) == MULAI
    assert baris.started_at is None
    assert antrean.oldest_queued() is None


@pytest.mark.parametrize("keadaan", ["running", "tidak_ada"])
def test_gagal_hanya_untuk_sesi_yang_masih_menunggu(antrean, db_session, keadaan):
    """Edge: sesi yang sudah jalan tidak bisa dijadikan failed dari antrean; id asing diabaikan."""
    # Arrange
    sesi = antrean.create_queued(_baru())
    sesi_id = sesi.id
    if keadaan == "running":
        antrean.start(sesi_id, items=ITEMS, product=_salinan(), started_at=MULAI)
    else:
        sesi_id = uuid.uuid4()

    # Act
    berhasil = antrean.fail(sesi_id, finished_at=MULAI)

    # Assert
    assert berhasil is False
    if keadaan == "running":
        assert db_session.get(MeasurementSession, sesi_id).status == SessionStatus.RUNNING


def test_provider_penilai_tersimpan_dan_terbaca_kembali(antrean, db_session):
    """Positif: judge_provider_id (kolom #89) ditulis saat Mulai dan dibawa ke giliran sesi."""
    # Arrange
    judge = uuid.UUID("00000000-0000-0000-0000-0000000000f8")

    # Act
    hasil = antrean.create_queued(_baru(judge_provider_id=judge))

    # Assert
    assert db_session.get(MeasurementSession, hasil.id).judge_provider_id == judge
    assert antrean.oldest_queued().new.judge_provider_id == judge


def test_tanpa_provider_penilai_kolomnya_kosong(antrean, db_session):
    """Edge: sesi tanpa penilai tetap tersimpan, kolomnya NULL sampai PBI-14."""
    # Act
    hasil = antrean.create_queued(_baru())

    # Assert
    assert db_session.get(MeasurementSession, hasil.id).judge_provider_id is None


def test_galat_selain_integrity_juga_dibatalkan_dan_sesi_tetap_menunggu(
    antrean, db_session, monkeypatch
):
    """Negatif: review #87, OperationalError atau DataError di tengah start() ikut dibatalkan.

    Tanpa rollback, UPDATE running tertinggal di transaksi dan session database
    yang dipakai runner berulang kali tidak bisa dipakai lagi pada putaran berikutnya.
    """
    # Arrange
    sesi = antrean.create_queued(_baru())

    def putus(*_args, **_kwargs):
        raise OperationalError("INSERT INTO case_snapshots", {}, Exception("koneksi putus"))

    # _freeze dipakai bersama sesi manual (SCRUM-230), jadi fungsi modul, bukan method.
    monkeypatch.setattr(repository, "_freeze", putus)

    # Act
    with pytest.raises(OperationalError):
        antrean.start(sesi.id, items=ITEMS, product=_salinan(), started_at=MULAI)

    # Assert
    status = db_session.scalar(
        select(MeasurementSession.status).where(MeasurementSession.id == sesi.id)
    )
    assert status == SessionStatus.QUEUED
    assert antrean.oldest_queued().id == sesi.id


def test_gagal_tetap_tercatat_setelah_transaksi_sebelumnya_galat(antrean, db_session):
    """Edge: review #87, sesi tetap bisa ditandai failed walau bacaan sebelumnya galat.

    Contohnya flush yang ditolak database: session ORM menunggu rollback, dan
    tanpa rollback di fail() sesi itu malah tertinggal di antrean.
    """
    # Arrange
    sesi = antrean.create_queued(_baru())
    db_session.add(MeasurementSessionSuite(session_id=sesi.id, suite_id=SUITE_A))
    with pytest.raises(IntegrityError):
        db_session.flush()

    # Act
    berhasil = antrean.fail(sesi.id, finished_at=MULAI)

    # Assert
    assert berhasil is True
    assert antrean.oldest_queued() is None
