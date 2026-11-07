"""Membuat sesi manual di PostgreSQL sungguhan. PBI-22 (SCRUM-176) AC1, AC2; SCRUM-230.

create_manual() menulis sesi, suite, snapshot, salinan produk, dan percobaan kosong dalam
satu transaksi, melewati trigger #89: kecocokan kasus dan versi di case_snapshot_items,
batas repetition_no di attempts, dan identitas sesi yang beku setelah keluar dari queued
(sesi manual tidak pernah queued, jadi beku sejak dibuat). Bila satu langkah gagal, tidak
ada yang tertinggal.

Session ORM diikat ke koneksi tes dengan savepoint, jadi commit di repository hanya
menutup savepoint dan semuanya dibatalkan di akhir tes.

Label: Positif = sesi dibuat utuh, Negatif = ditolak tanpa sisa.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.runs import repository, schemas, session_ports
from tests.measurement_pg import buat_admin, buat_kasus, buat_produk

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diisi"),
]


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


def _sesi_baru(admin_id, produk_id, suite_ids, repetitions=2) -> session_ports.NewSession:
    return session_ports.NewSession(
        label="Justisio November",
        purpose=schemas.MeasurementPurpose.TRIAL,
        product_id=produk_id,
        suite_ids=tuple(suite_ids),
        case_filter=schemas.CaseFilter.DEV,
        repetitions=repetitions,
        created_by=admin_id,
    )


def _salinan(produk_id) -> session_ports.ProductCopy:
    return session_ports.ProductCopy(
        product_id=produk_id,
        name="Justisio",
        measurement_type="manual",
        provider_type=None,
        base_url=None,
        model_name=None,
        model_params=None,
        price_input_usd_per_mtok=None,
        price_output_usd_per_mtok=None,
        monthly_budget_idr=None,
        vendor_name="Justisio",
        site_url="https://justisio.com",
    )


def _item(case_id, version_id) -> session_ports.SnapshotItem:
    return session_ports.SnapshotItem(
        case_id=case_id,
        case_version_id=version_id,
        split_tag="dev",
        content={"version_no": 1, "sections": {"split_tag": "dev"}},
    )


def _suite_kasus(conn, case_id) -> uuid.UUID:
    return conn.execute(
        text("SELECT suite_id FROM cases WHERE id = :id"), {"id": case_id}
    ).scalar_one()


def _jumlah_sesi(conn) -> int:
    return conn.execute(text("SELECT count(*) FROM measurement_sessions")).scalar_one()


def test_sesi_manual_tersimpan_utuh_melewati_trigger(conn, db):
    """Positif: AC1, AC2, sesi Belum terisi dengan snapshot dan 2 kasus x 2 percobaan kosong."""
    # Arrange
    admin_id = buat_admin(conn)
    produk_id = buat_produk(conn, admin_id)
    kasus = [buat_kasus(conn, admin_id) for _ in range(2)]
    suite_ids = [_suite_kasus(conn, case_id) for case_id, _ in kasus]

    # Act
    sesi_id = repository.ManualSessionRepository(db).create_manual(
        _sesi_baru(admin_id, produk_id, suite_ids),
        items=[_item(*k) for k in kasus],
        product=_salinan(produk_id),
    )

    # Assert
    baris = conn.execute(
        text(
            "SELECT session_type::text, status::text, queued_at FROM measurement_sessions "
            "WHERE id = :id"
        ),
        {"id": sesi_id},
    ).one()
    assert tuple(baris) == ("manual", "not_filled", None)
    percobaan = conn.execute(
        text(
            "SELECT source::text, status::text, count(*) FROM attempts "
            "WHERE session_id = :id GROUP BY 1, 2"
        ),
        {"id": sesi_id},
    ).all()
    assert [tuple(p) for p in percobaan] == [("manual", "pending", 4)]
    assert conn.execute(
        text("SELECT vendor_name, site_url FROM session_product_copies WHERE session_id = :id"),
        {"id": sesi_id},
    ).one() == ("Justisio", "https://justisio.com")


def test_kasus_dengan_versi_yang_tidak_cocok_tidak_meninggalkan_apa_pun(conn, db):
    """Negatif: trigger #89 menolak item snapshot; sesi, suite, dan salinan ikut batal."""
    # Arrange
    admin_id = buat_admin(conn)
    produk_id = buat_produk(conn, admin_id)
    (case_id, _), (_, versi_lain) = buat_kasus(conn, admin_id), buat_kasus(conn, admin_id)
    sebelum = _jumlah_sesi(conn)

    # Act
    with pytest.raises(DBAPIError):
        repository.ManualSessionRepository(db).create_manual(
            _sesi_baru(admin_id, produk_id, [_suite_kasus(conn, case_id)]),
            items=[_item(case_id, versi_lain)],
            product=_salinan(produk_id),
        )

    # Assert
    assert _jumlah_sesi(conn) == sebelum
    assert (
        conn.execute(
            text("SELECT count(*) FROM session_product_copies WHERE product_id = :id"),
            {"id": produk_id},
        ).scalar_one()
        == 0
    )
