"""Hitungan kasus selesai di PostgreSQL sungguhan. SCRUM-77 SCRUM-265.

repository.case_completion memakai GROUP BY dua tingkat dan CASE; tes SQLite sudah
memeriksa aturannya, tes ini memeriksa query yang sama di PostgreSQL dengan trigger
#89 (snapshot) dan attempts_guard_status (SCRUM-214) aktif. Status percobaan diubah
lewat jalur yang sah: pending -> running -> succeeded | technical_failure, lalu
coba ulang technical_failure -> pending.

Session ORM diikat ke koneksi tes dengan savepoint; semuanya dibatalkan di akhir test.
Label: Positif = hitungan benar, Edge = coba ulang.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.runs import repository, schemas, session_ports
from tests.measurement_pg import buat_admin, buat_kasus, buat_produk

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]

JAM = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)


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
    with Session(bind=conn, join_transaction_mode="create_savepoint") as sesi:
        yield sesi


@pytest.fixture
def sesi_id(conn, db) -> uuid.UUID:
    """Sesi otomatis berjalan: 2 kasus approved x 2 pengulangan, semua attempts pending."""
    admin_id = buat_admin(conn)
    produk_id = buat_produk(conn, admin_id)
    kasus = [buat_kasus(conn, admin_id) for _ in range(2)]
    jam = iter(JAM + timedelta(minutes=menit) for menit in range(10))
    antrean = repository.SessionQueueRepository(db, clock=lambda: next(jam))
    baru = antrean.create_queued(
        session_ports.NewSession(
            label="Run 265",
            purpose=schemas.MeasurementPurpose.TRIAL,
            product_id=produk_id,
            suite_ids=(),
            case_filter=schemas.CaseFilter.BOTH,
            repetitions=2,
            created_by=admin_id,
        )
    ).id
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
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("10"),
        monthly_budget_idr=Decimal("500000"),
    )
    assert antrean.start(baru, items=items, product=salinan, started_at=JAM) is True
    return baru


def _attempt_ids(conn, sesi_id: uuid.UUID) -> list[uuid.UUID]:
    """Urut per kasus lalu pengulangan: [k1r1, k1r2, k2r1, k2r2]."""
    return list(
        conn.execute(
            text(
                "SELECT id FROM attempts WHERE session_id = :id "
                "ORDER BY snapshot_item_id, repetition_no"
            ),
            {"id": sesi_id},
        ).scalars()
    )


def _selesaikan(conn, attempt_id: uuid.UUID, hasil: str) -> None:
    for status in ("running", hasil):
        conn.execute(
            text("UPDATE attempts SET status = CAST(:s AS attempt_status_enum) WHERE id = :id"),
            {"s": status, "id": attempt_id},
        )


def test_kasus_selesai_bila_semua_pengulangannya_selesai(conn, db, sesi_id):
    """Positif: kasus 1 selesai (berhasil + gagal teknis), kasus 2 baru satu pengulangan."""
    # Arrange
    k1r1, k1r2, k2r1, _ = _attempt_ids(conn, sesi_id)
    _selesaikan(conn, k1r1, "succeeded")
    _selesaikan(conn, k1r2, "technical_failure")
    _selesaikan(conn, k2r1, "succeeded")

    # Act
    hasil = repository.case_completion(db, [sesi_id])

    # Assert
    assert hasil == {sesi_id: (1, 2)}


def test_coba_ulang_membuat_kasus_kembali_belum_selesai(conn, db, sesi_id):
    """Edge: technical_failure -> pending (coba ulang) menurunkan kasus selesai."""
    # Arrange
    ids = _attempt_ids(conn, sesi_id)
    for attempt_id in ids[:-1]:
        _selesaikan(conn, attempt_id, "succeeded")
    _selesaikan(conn, ids[-1], "technical_failure")
    assert repository.case_completion(db, [sesi_id]) == {sesi_id: (2, 2)}

    # Act
    conn.execute(
        text("UPDATE attempts SET status = 'pending' WHERE id = :id"),
        {"id": ids[-1]},
    )
    hasil = repository.case_completion(db, [sesi_id])

    # Assert
    assert hasil == {sesi_id: (1, 2)}
