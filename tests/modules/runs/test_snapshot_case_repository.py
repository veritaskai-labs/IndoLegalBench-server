"""Test pembacaan item snapshot satu sesi. SCRUM-225."""

import uuid

import pytest

from app.modules.auth.models import User
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.runs import repository
from app.modules.runs.models import (
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionPurpose,
    SessionType,
    SplitFilter,
)
from app.shared.security import Role


@pytest.fixture
def admin(db_session):
    user = User(
        email="admin-225@veritask.test",
        name="Admin",
        role=Role.ADMIN,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session, admin):
    prod = AiProduct(
        name="Justisio",
        created_by=admin.id,
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Justisio",
        site_url="https://justisio.test",
    )
    db_session.add(prod)
    db_session.commit()
    return prod


def _sesi(db_session, produk, admin, label):
    row = MeasurementSession(
        label=label,
        purpose=SessionPurpose.TRIAL,
        session_type=SessionType.AUTOMATIC,
        product_id=produk.id,
        repetitions=3,
        split_filter=SplitFilter.BOTH,
        created_by=admin.id,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def sesi(db_session, produk, admin):
    return _sesi(db_session, produk, admin, "Uji coba November")


@pytest.fixture
def snapshot(db_session, sesi):
    row = CaseSnapshot(session_id=sesi.id)
    db_session.add(row)
    db_session.commit()
    return row


def _item(db_session, snapshot, *, case_code, split_tag, category):
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body={
            "version_no": 1,
            "status": "approved",
            "sections": {
                "case_code": case_code,
                "split_tag": split_tag,
                "category": category,
            },
        },
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_item_sesi_terbaca_apa_adanya(db_session, sesi, snapshot):
    item = _item(
        db_session,
        snapshot,
        case_code="PJK-001",
        split_tag="dev",
        category="pajak",
    )

    hasil = repository.snapshot_case_items(db_session, sesi.id)

    assert len(hasil) == 1
    item_id, body = hasil[0]
    assert item_id == item.id
    assert body["sections"]["category"] == "pajak"
    assert body["sections"]["split_tag"] == "dev"


def test_item_sesi_lain_tidak_ikut_terbawa(
    db_session, produk, admin, sesi, snapshot
):
    _item(
        db_session,
        snapshot,
        case_code="PJK-001",
        split_tag="dev",
        category="pajak",
    )

    sesi_lain = _sesi(db_session, produk, admin, "Sesi lain")
    snapshot_lain = CaseSnapshot(session_id=sesi_lain.id)
    db_session.add(snapshot_lain)
    db_session.commit()

    _item(
        db_session,
        snapshot_lain,
        case_code="PJK-999",
        split_tag="test",
        category="aset",
    )

    hasil = repository.snapshot_case_items(db_session, sesi.id)

    assert [body["sections"]["case_code"] for _, body in hasil] == [
        "PJK-001"
    ]


def test_urutannya_tetap_antar_pembacaan(db_session, sesi, snapshot):
    for nomor in range(4):
        _item(
            db_session,
            snapshot,
            case_code=f"PJK-00{nomor}",
            split_tag="dev",
            category=None,
        )

    pertama = repository.snapshot_case_items(db_session, sesi.id)
    kedua = repository.snapshot_case_items(db_session, sesi.id)

    assert [item_id for item_id, _ in pertama] == [
        item_id for item_id, _ in kedua
    ]
    assert len(pertama) == 4


def test_sesi_tanpa_snapshot_tidak_punya_item(
    db_session, produk, admin
):
    sesi_baru = _sesi(db_session, produk, admin, "Belum jalan")

    hasil = repository.snapshot_case_items(db_session, sesi_baru.id)

    assert hasil == []