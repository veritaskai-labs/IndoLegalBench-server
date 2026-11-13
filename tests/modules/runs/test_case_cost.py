"""Biaya per kasus dari call log yang sudah tersimpan. SCRUM-176 SCRUM-298."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.modules.runs.models import Attempt, CallLog, SessionStatus, SessionType
from app.modules.runs.repository import SessionListRepository
from app.modules.runs.session_detail import CaseRow, _case
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_grade import _setup_sesi_lengkap

SAAT = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)


def _produk(db_session):
    from app.modules.auth.models import User

    user = User(email="biaya-kasus@veritask.test", name="Admin", role=Role.ADMIN)
    produk = _otomatis("Produk Biaya Kasus")
    db_session.add_all([user, produk])
    db_session.commit()
    return user, produk


def _log(db_session, attempt_id, product_id, biaya, try_no=1):
    db_session.add(
        CallLog(
            attempt_id=attempt_id,
            product_id=product_id,
            started_at=SAAT,
            ended_at=SAAT,
            duration_ms=10,
            cost_idr=biaya,
            usd_idr_rate=Decimal("16000"),
            model_version="model-v1",
            result="ok",
            raw={},
            try_no=try_no,
        )
    )
    db_session.commit()


def test_kasus_otomatis_menjumlahkan_pengulangan_dan_coba_ulang(db_session):
    """Positif: biaya kasus adalah jumlah call log seluruh attempt item itu."""
    pembuat, produk = _produk(db_session)
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    attempts = list(db_session.scalars(select(Attempt).where(Attempt.session_id == sesi.id)))
    target = attempts[0]
    _log(db_session, target.id, produk.id, Decimal("4.00"), try_no=1)
    _log(db_session, target.id, produk.id, Decimal("1.25"), try_no=2)

    cases = {
        row.snapshot_item_id: row.cost_idr
        for row in SessionListRepository(db_session).detail(sesi.id).cases
    }

    assert cases[target.snapshot_item_id] == Decimal("5.25")
    lain = next(biaya for item_id, biaya in cases.items() if item_id != target.snapshot_item_id)
    assert lain is None


def test_kasus_manual_tanpa_panggilan_bernilai_nol(db_session):
    """Positif: kasus manual yang belum punya log tetap 0, bukan kosong."""
    pembuat, produk = _produk(db_session)
    sesi = _setup_sesi_lengkap(db_session, produk, pembuat, status=SessionStatus.FINISHED)
    sesi.session_type = SessionType.MANUAL
    db_session.commit()

    biaya = [row.cost_idr for row in SessionListRepository(db_session).detail(sesi.id).cases]

    assert biaya == [Decimal("0.00"), Decimal("0.00")]


def test_kasus_test_yang_disamarkan_tetap_menunjukkan_biaya():
    """Positif: biaya bukan isi kasus, jadi tetap tampil saat teksnya disamarkan."""
    case = CaseRow(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        body={"sections": {"split_tag": "test", "case_code": "T-1", "identity.title": "Rahasia"}},
        attempts=[],
        cost_idr=Decimal("2.50"),
    )

    hasil = _case(case, viewer_role=Role.DEVELOPER_AI)

    assert hasil.masked is True
    assert hasil.question is None
    assert hasil.cost_idr == Decimal("2.50")
