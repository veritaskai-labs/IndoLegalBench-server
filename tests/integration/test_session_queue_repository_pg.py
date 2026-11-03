"""Antrean dan awal sesi di PostgreSQL sungguhan. SCRUM-76 SCRUM-207.

PBI-11 AC2, AC3, AC6 dan D3 langkah 25-28 di atas index dan trigger milik #89:
partial unique index "satu sesi otomatis running", trigger kecocokan kasus dan
versi di case_snapshot_items, serta trigger batas repetition_no di attempts.
start() harus lolos semua itu dalam satu transaksi, atau tidak meninggalkan
apa pun.

Session ORM diikat ke koneksi tes dengan savepoint, jadi commit di repository
hanya menutup savepoint dan semuanya dibatalkan di akhir test.

Label: Positif = sesi mulai utuh, Negatif = ditolak tanpa sisa.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.modules.runs import repository, schemas, session_ports
from tests.measurement_pg import buat_admin, buat_kasus, buat_produk

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

JAM = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)


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


@pytest.fixture
def siap(conn, db):
    """Admin, produk, dua kasus approved sungguhan, dan repository dengan jam maju."""
    admin_id = buat_admin(conn)
    produk_id = buat_produk(conn, admin_id)
    kasus = [buat_kasus(conn, admin_id) for _ in range(2)]
    jam = iter(JAM + timedelta(minutes=menit) for menit in range(10))
    antrean = repository.SessionQueueRepository(db, clock=lambda: next(jam))
    items = [
        session_ports.SnapshotItem(
            case_id=case_id,
            case_version_id=version_id,
            split_tag="dev",
            content={"version_no": 1, "sections": {"split_tag": "dev"}},
        )
        for case_id, version_id in kasus
    ]
    salinan = session_ports.ProductCopy(
        product_id=produk_id,
        name="AiYU",
        measurement_type="automatic",
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1/chat/completions",
        model_name="deepseek-flash",
        model_params=None,
        rate_limit_per_minute=30,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("10"),
        monthly_budget_idr=Decimal("500000"),
    )

    def baru(label: str, repetitions: int = 2) -> uuid.UUID:
        return antrean.create_queued(
            session_ports.NewSession(
                label=label,
                purpose=schemas.MeasurementPurpose.OFFICIAL,
                product_id=produk_id,
                suite_ids=(),
                case_filter=schemas.CaseFilter.BOTH,
                repetitions=repetitions,
                created_by=admin_id,
            )
        ).id

    return SimpleNamespace(antrean=antrean, items=items, salinan=salinan, baru=baru)


def _satu(conn, sql: str, sesi_id: uuid.UUID):
    return conn.execute(text(sql), {"id": sesi_id}).scalar_one()


def test_start_lolos_trigger_dan_mencatat_semuanya(conn, siap):
    """Positif: snapshot, salinan, dan attempts lolos trigger #89; model_params NULL sungguhan."""
    # Arrange
    sesi_id = siap.baru("Run PG", repetitions=3)

    # Act
    berhasil = siap.antrean.start(sesi_id, items=siap.items, product=siap.salinan, started_at=JAM)

    # Assert
    assert berhasil is True
    assert _satu(conn, "SELECT status::text FROM measurement_sessions WHERE id = :id", sesi_id) == (
        "running"
    )
    assert _satu(conn, "SELECT count(*) FROM attempts WHERE session_id = :id", sesi_id) == 6
    assert (
        _satu(
            conn,
            "SELECT count(*) FROM case_snapshot_items i JOIN case_snapshots s "
            "ON s.id = i.snapshot_id WHERE s.session_id = :id",
            sesi_id,
        )
        == 2
    )
    assert _satu(
        conn,
        "SELECT model_params IS NULL FROM session_product_copies WHERE session_id = :id",
        sesi_id,
    )


def test_index_satu_running_menolak_sesi_kedua_tanpa_sisa(conn, siap):
    """Negatif: AC3 ditegakkan database; sesi kedua tetap queued tanpa snapshot atau salinan."""
    # Arrange
    pertama = siap.baru("Pertama")
    kedua = siap.baru("Kedua")
    siap.antrean.start(pertama, items=siap.items, product=siap.salinan, started_at=JAM)

    # Act
    berhasil = siap.antrean.start(kedua, items=siap.items, product=siap.salinan, started_at=JAM)

    # Assert
    assert berhasil is False
    assert _satu(conn, "SELECT status::text FROM measurement_sessions WHERE id = :id", kedua) == (
        "queued"
    )
    for tabel in ("case_snapshots", "session_product_copies", "attempts"):
        assert _satu(conn, f"SELECT count(*) FROM {tabel} WHERE session_id = :id", kedua) == 0


def test_kasus_dan_versi_yang_tidak_cocok_ditolak_trigger_dan_dibatalkan(conn, siap):
    """Negatif: item rusak (versi milik kasus lain) dihentikan trigger; sesi tetap queued."""
    # Arrange
    sesi_id = siap.baru("Rusak")
    (pertama, kedua) = siap.items
    rusak = session_ports.SnapshotItem(
        case_id=pertama.case_id,
        case_version_id=kedua.case_version_id,
        split_tag="dev",
        content={},
    )

    # Act
    with pytest.raises(IntegrityError):
        siap.antrean.start(sesi_id, items=[rusak], product=siap.salinan, started_at=JAM)

    # Assert
    assert _satu(conn, "SELECT status::text FROM measurement_sessions WHERE id = :id", sesi_id) == (
        "queued"
    )
    assert _satu(conn, "SELECT count(*) FROM case_snapshots WHERE session_id = :id", sesi_id) == 0


def test_sesi_menunggu_boleh_ditandai_gagal_oleh_trigger(conn, siap):
    """Positif: trigger #89 mengizinkan queued -> failed dengan finished_at, tanpa snapshot."""
    # Arrange
    sesi_id = siap.baru("Produk dihapus")

    # Act
    berhasil = siap.antrean.fail(sesi_id, finished_at=JAM)

    # Assert
    assert berhasil is True
    assert _satu(conn, "SELECT status::text FROM measurement_sessions WHERE id = :id", sesi_id) == (
        "failed"
    )
    assert _satu(
        conn, "SELECT finished_at IS NOT NULL FROM measurement_sessions WHERE id = :id", sesi_id
    )


def test_gagal_tetap_tercatat_setelah_bacaan_yang_membatalkan_transaksi(conn, db, siap):
    """Edge: review #87, di PostgreSQL bacaan yang galat membatalkan seluruh transaksi.

    Itulah yang terjadi bila bacaan snapshot gagal sebelum start(); fail() harus
    tetap bisa menandai sesi, bukan berhenti dengan InFailedSqlTransaction.
    """
    # Arrange
    sesi_id = siap.baru("Bacaan galat")
    with pytest.raises(ProgrammingError):
        db.execute(text("SELECT * FROM tabel_yang_tidak_ada"))

    # Act
    berhasil = siap.antrean.fail(sesi_id, finished_at=JAM)

    # Assert
    assert berhasil is True
    assert _satu(conn, "SELECT status::text FROM measurement_sessions WHERE id = :id", sesi_id) == (
        "failed"
    )
