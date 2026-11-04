"""Hitungan progres satu sesi dari tabel pengukuran. SCRUM-77 SCRUM-215.

PBI-12 AC5: layar progres membaca jumlah panggilan selesai, jumlah gagal, dan
biaya berjalan satu sesi. Panggilan yang technical_failure tetap terhitung
selesai, karena sudah diproses dan tidak diulang.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.auth.models import User
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.runs import repository
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionPurpose,
    SessionType,
    SplitFilter,
)
from app.shared.security import Role


@pytest.fixture
def admin(db_session) -> User:
    user = User(email="admin-215@veritask.test", name="Admin", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session, admin) -> AiProduct:
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


@pytest.fixture
def sesi(db_session, produk, admin) -> MeasurementSession:
    row = MeasurementSession(
        label="Uji coba Oktober",
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
def snapshot(db_session, sesi) -> CaseSnapshot:
    """Satu snapshot per sesi, isinya satu item per kasus."""
    row = CaseSnapshot(session_id=sesi.id)
    db_session.add(row)
    db_session.commit()
    return row


def _attempt(
    db_session,
    sesi,
    snapshot,
    status: AttemptStatus,
    repetition_no: int,
    updated_at: datetime | None = None,
) -> Attempt:
    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body={},
    )
    db_session.add(item)
    db_session.flush()
    nilai = {
        "session_id": sesi.id,
        "snapshot_item_id": item.id,
        "repetition_no": repetition_no,
        "source": AttemptSource.AUTOMATIC,
        "status": status,
    }
    # updated_at diisi saat INSERT: default kolomnya baru jalan kalau tidak diberi nilai.
    if updated_at is not None:
        nilai["updated_at"] = updated_at
    row = Attempt(**nilai)
    db_session.add(row)
    db_session.flush()
    return row


def _biaya(db_session, attempt: Attempt, produk: AiProduct, jumlah: str) -> None:
    saat = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    db_session.add(
        CallLog(
            attempt_id=attempt.id,
            product_id=produk.id,
            started_at=saat,
            ended_at=saat,
            duration_ms=120,
            cost_idr=Decimal(jumlah),
            usd_idr_rate=Decimal("16000"),
            model_version="deepseek-flash",
            result="ok",
            raw={},
        )
    )


def test_hitungan_sesi_dari_attempt_dan_call_log(db_session, sesi, snapshot, produk):
    # Arrange: 3 panggilan, 2 sudah diproses, 1 di antaranya gagal teknis
    sukses = _attempt(db_session, sesi, snapshot, AttemptStatus.SUCCEEDED, 1)
    gagal = _attempt(db_session, sesi, snapshot, AttemptStatus.TECHNICAL_FAILURE, 2)
    _attempt(db_session, sesi, snapshot, AttemptStatus.PENDING, 3)
    _biaya(db_session, sukses, produk, "1500.00")
    _biaya(db_session, gagal, produk, "2500.00")
    db_session.commit()

    # Act
    hasil = repository.session_progress(db_session, sesi.id)

    # Assert
    assert hasil.total_calls == 3
    assert hasil.completed_calls == 2
    assert hasil.failed_calls == 1
    assert hasil.running_cost_idr == Decimal("4000.00")


def test_waktu_aktivitas_terakhir_dari_baris_attempt(db_session, sesi, snapshot):
    """Baris sesi tidak ikut diperbarui tiap panggilan, jadi waktunya diambil dari attempt."""
    # Arrange
    lama = datetime(2026, 11, 3, 10, 0)
    baru = datetime(2026, 11, 3, 10, 30)
    _attempt(db_session, sesi, snapshot, AttemptStatus.SUCCEEDED, 1, updated_at=lama)
    _attempt(db_session, sesi, snapshot, AttemptStatus.SUCCEEDED, 2, updated_at=baru)
    db_session.commit()

    # Act
    hasil = repository.session_progress(db_session, sesi.id)

    # Assert
    assert hasil.last_attempt_at == baru


def test_sesi_tanpa_attempt_belum_punya_waktu_aktivitas(db_session, sesi):
    # Arrange + Act: sesi masih mengantre, belum ada panggilan sama sekali
    hasil = repository.session_progress(db_session, sesi.id)

    # Assert
    assert hasil.last_attempt_at is None
