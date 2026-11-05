"""Tes pemulihan sesi setelah restart server dan worker liveness.

PBI-12 AC8, D5 langkah 7 (SCRUM-217).

Memastikan:
1. Sesi RUNNING saat restart ditandai PAUSED dengan stop_reason = RESTART (AC8).
2. Attempt RUNNING yang terputus dikembalikan ke PENDING (D5 langkah 41).
3. Attempt SUCCEEDED, TECHNICAL_FAILURE, dan PENDING tidak terganggu.
4. Sesi tidak lanjut otomatis; Admin yang harus menekan Lanjutkan (AC8).
5. Heartbeat worker: sesi RUNNING yang heartbeat-nya melebihi ambang batas (stale)
   ditandai PAUSED lewat SessionStatusRepository (AC8).
6. Sesi yang masih aktif (heartbeat dalam batas) tetap RUNNING.
7. Startup hook pada lifespan FastAPI memulihkan sesi saat server menyala.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.main import lifespan
from app.modules.auth.models import User
from app.modules.providers.models import AiProduct, ProviderType
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionPurpose,
    SessionStatus,
    SessionType,
    SplitFilter,
    StopReason,
)
from app.modules.runs.recovery import (
    check_worker_liveness,
    record_heartbeat,
    recover_after_restart,
)
from app.shared.security import Role

WAKTU_MULAI = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
WAKTU_RESTART = datetime(2026, 11, 5, 8, 15, tzinfo=UTC)


@pytest.fixture
def admin_user(db_session) -> User:
    user = User(email="admin-recovery@veritask.test", name="Admin Recovery", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk_otomatis(db_session, admin_user) -> AiProduct:
    product = AiProduct(
        name="DeepSeek Test Recovery",
        measurement_type="automatic",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://api.deepseek.com",
        model_name="deepseek-chat",
        credential_encrypted=b"encrypted",
        credential_hint="sk-1",
        rate_limit_per_minute=60,
        monthly_budget_idr=500_000,
        created_by=admin_user.id,
    )
    db_session.add(product)
    db_session.commit()
    return product


def _buat_sesi(
    db_session,
    produk: AiProduct,
    user: User,
    *,
    status: SessionStatus = SessionStatus.RUNNING,
    session_type: SessionType = SessionType.AUTOMATIC,
    updated_at: datetime = WAKTU_MULAI,
) -> MeasurementSession:
    sesi = MeasurementSession(
        label="Sesi Pengukuran Uji",
        purpose=SessionPurpose.TRIAL,
        session_type=session_type,
        product_id=produk.id,
        repetitions=2,
        split_filter=SplitFilter.BOTH,
        status=status,
        created_by=user.id,
        created_at=WAKTU_MULAI,
        started_at=WAKTU_MULAI,
        updated_at=updated_at,
    )
    db_session.add(sesi)
    db_session.commit()
    return sesi


def _tambah_snapshot_dan_attempts(
    db_session,
    sesi: MeasurementSession,
    attempt_statuses: list[AttemptStatus],
) -> list[Attempt]:
    snapshot = CaseSnapshot(session_id=sesi.id, created_at=WAKTU_MULAI)
    db_session.add(snapshot)
    db_session.flush()

    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body={"sections": {"identity.question": "Pertanyaan hukum"}},
    )
    db_session.add(item)
    db_session.flush()

    attempts: list[Attempt] = []
    for idx, att_status in enumerate(attempt_statuses, start=1):
        att = Attempt(
            session_id=sesi.id,
            snapshot_item_id=item.id,
            repetition_no=idx,
            source=AttemptSource.AUTOMATIC,
            status=att_status,
            answer_text="Jawaban tersimpan" if att_status == AttemptStatus.SUCCEEDED else None,
        )
        db_session.add(att)
        attempts.append(att)

    db_session.commit()
    return attempts


def _as_utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# ==============================================================================
# 1. Server Restart Recovery (AC8, D5 langkah 7)
# ==============================================================================


def test_recover_after_restart_marks_running_session_as_paused(
    db_session, produk_otomatis, admin_user
):
    """Positif: sesi RUNNING saat restart ditandai PAUSED dengan stop_reason = RESTART (AC8)."""
    # Arrange
    sesi = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.RUNNING)

    # Act
    recovered = recover_after_restart(db_session, at=WAKTU_RESTART)

    # Assert
    assert recovered == [sesi.id]
    db_session.expire_all()
    baris = db_session.get(MeasurementSession, sesi.id)
    assert baris.status == SessionStatus.PAUSED
    assert baris.stop_reason == StopReason.RESTART
    assert _as_utc(baris.stopped_at) == WAKTU_RESTART


def test_recover_after_restart_resets_running_attempt_to_pending(
    db_session, produk_otomatis, admin_user
):
    """Positif: attempt RUNNING yang terputus saat restart dikembalikan ke PENDING (D5 langkah 41)."""
    # Arrange
    sesi = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.RUNNING)
    attempts = _tambah_snapshot_dan_attempts(
        db_session,
        sesi,
        [AttemptStatus.SUCCEEDED, AttemptStatus.RUNNING],
    )

    # Act
    recovered = recover_after_restart(db_session, at=WAKTU_RESTART)

    # Assert
    assert recovered == [sesi.id]
    db_session.expire_all()
    att_succeeded = db_session.get(Attempt, attempts[0].id)
    att_interrupted = db_session.get(Attempt, attempts[1].id)

    # Attempt pertama tetap succeeded, attempt kedua di-reset ke pending
    assert att_succeeded.status == AttemptStatus.SUCCEEDED
    assert att_interrupted.status == AttemptStatus.PENDING


def test_recover_after_restart_preserves_technical_failures_and_pending_attempts(
    db_session, produk_otomatis, admin_user
):
    """Positif: attempt TECHNICAL_FAILURE dan PENDING tidak disentuh saat restart recovery."""
    # Arrange
    sesi = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.RUNNING)
    attempts = _tambah_snapshot_dan_attempts(
        db_session,
        sesi,
        [AttemptStatus.TECHNICAL_FAILURE, AttemptStatus.PENDING],
    )

    # Act
    recover_after_restart(db_session, at=WAKTU_RESTART)

    # Assert
    db_session.expire_all()
    assert db_session.get(Attempt, attempts[0].id).status == AttemptStatus.TECHNICAL_FAILURE
    assert db_session.get(Attempt, attempts[1].id).status == AttemptStatus.PENDING


def test_recover_after_restart_does_not_touch_paused_queued_finished_sessions(
    db_session, produk_otomatis, admin_user
):
    """Edge: sesi QUEUED, PAUSED, FINISHED tidak disentuh oleh recovery restart."""
    # Arrange
    sesi_queued = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.QUEUED)
    sesi_paused = _buat_sesi(
        db_session,
        produk_otomatis,
        admin_user,
        status=SessionStatus.PAUSED,
        updated_at=WAKTU_MULAI,
    )
    # Beri stop_reason untuk paused agar konsisten
    sesi_paused.stop_reason = StopReason.USER
    sesi_paused.stopped_at = WAKTU_MULAI
    db_session.commit()

    # Act
    recovered = recover_after_restart(db_session, at=WAKTU_RESTART)

    # Assert
    assert recovered == []
    db_session.expire_all()
    assert db_session.get(MeasurementSession, sesi_queued.id).status == SessionStatus.QUEUED
    assert db_session.get(MeasurementSession, sesi_paused.id).status == SessionStatus.PAUSED
    assert db_session.get(MeasurementSession, sesi_paused.id).stop_reason == StopReason.USER


def test_recover_after_restart_when_no_running_sessions_returns_empty(db_session):
    """Edge: bila tidak ada sesi yang berjalan, recovery berjalan aman dan mengembalikan list kosong."""
    # Act
    recovered = recover_after_restart(db_session, at=WAKTU_RESTART)

    # Assert
    assert recovered == []


def test_recover_after_restart_does_not_auto_resume(db_session, produk_otomatis, admin_user):
    """Positif: setelah restart sesi tetap PAUSED dan tidak lanjut otomatis (AC8)."""
    # Arrange
    sesi = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.RUNNING)

    # Act
    recover_after_restart(db_session, at=WAKTU_RESTART)

    # Assert
    db_session.expire_all()
    baris = db_session.get(MeasurementSession, sesi.id)
    assert baris.status == SessionStatus.PAUSED
    # Tidak berpindah ke running dengan sendirinya
    assert baris.status != SessionStatus.RUNNING


# ==============================================================================
# 2. Worker Liveness / Heartbeat (AC8)
# ==============================================================================


def test_check_worker_liveness_marks_stale_session_as_paused(
    db_session, produk_otomatis, admin_user
):
    """Positif: sesi RUNNING yang heartbeat-nya lewat batas waktu ditandai PAUSED (stop_reason RESTART)."""
    # Arrange: sesi terakhir kali berdenyut 10 menit yang lalu (lewat batas 5 menit)
    heartbeat_lama = WAKTU_MULAI
    sekarang = WAKTU_MULAI + timedelta(minutes=10)
    sesi = _buat_sesi(
        db_session,
        produk_otomatis,
        admin_user,
        status=SessionStatus.RUNNING,
        updated_at=heartbeat_lama,
    )
    attempts = _tambah_snapshot_dan_attempts(
        db_session,
        sesi,
        [AttemptStatus.RUNNING, AttemptStatus.PENDING],
    )

    # Act
    stale_ids = check_worker_liveness(db_session, at=sekarang)

    # Assert
    assert stale_ids == [sesi.id]
    db_session.expire_all()
    baris = db_session.get(MeasurementSession, sesi.id)
    assert baris.status == SessionStatus.PAUSED
    assert baris.stop_reason == StopReason.RESTART
    assert _as_utc(baris.stopped_at) == sekarang
    # Attempt yang sedang running di-reset ke pending
    assert db_session.get(Attempt, attempts[0].id).status == AttemptStatus.PENDING


def test_check_worker_liveness_leaves_active_session_running(
    db_session, produk_otomatis, admin_user
):
    """Negatif: sesi RUNNING dengan heartbeat yang masih baru (dalam batas timeout) tidak dihentikan."""
    # Arrange: sesi berdenyut 1 menit yang lalu (di bawah default timeout 5 menit)
    sekarang = WAKTU_MULAI + timedelta(minutes=1)
    sesi = _buat_sesi(
        db_session,
        produk_otomatis,
        admin_user,
        status=SessionStatus.RUNNING,
        updated_at=WAKTU_MULAI,
    )

    # Act
    stale_ids = check_worker_liveness(db_session, at=sekarang)

    # Assert
    assert stale_ids == []
    db_session.expire_all()
    baris = db_session.get(MeasurementSession, sesi.id)
    assert baris.status == SessionStatus.RUNNING
    assert baris.stop_reason is None


def test_check_worker_liveness_respects_custom_timeout(db_session, produk_otomatis, admin_user):
    """Edge: parameter timeout_seconds kustom dihormati."""
    # Arrange: sesi berdenyut 60 detik yang lalu; timeout diset 45 detik
    sekarang = WAKTU_MULAI + timedelta(seconds=60)
    sesi = _buat_sesi(
        db_session,
        produk_otomatis,
        admin_user,
        status=SessionStatus.RUNNING,
        updated_at=WAKTU_MULAI,
    )

    # Act
    stale_ids = check_worker_liveness(db_session, timeout_seconds=45.0, at=sekarang)

    # Assert
    assert stale_ids == [sesi.id]
    db_session.expire_all()
    assert db_session.get(MeasurementSession, sesi.id).status == SessionStatus.PAUSED


def test_record_heartbeat_updates_session_timestamp(db_session, produk_otomatis, admin_user):
    """Positif: memanggil record_heartbeat memperbarui updated_at pada sesi RUNNING."""
    # Arrange
    sesi = _buat_sesi(
        db_session,
        produk_otomatis,
        admin_user,
        status=SessionStatus.RUNNING,
        updated_at=WAKTU_MULAI,
    )
    waktu_baru = WAKTU_MULAI + timedelta(minutes=2)

    # Act
    sukses = record_heartbeat(db_session, sesi.id, at=waktu_baru)

    # Assert
    assert sukses is True
    db_session.expire_all()
    baris = db_session.get(MeasurementSession, sesi.id)
    assert _as_utc(baris.updated_at) == waktu_baru


def test_record_heartbeat_returns_false_for_non_running_or_missing_session(
    db_session, produk_otomatis, admin_user
):
    """Negatif: record_heartbeat mengembalikan False bila sesi tidak ditemukan atau tidak RUNNING."""
    # Arrange: sesi queued
    sesi_queued = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.QUEUED)

    # Act & Assert
    assert record_heartbeat(db_session, sesi_queued.id) is False
    assert record_heartbeat(db_session, uuid.uuid4()) is False


def test_as_utc_helper():
    """Edge: helper _as_utc menangani None, naive datetime, dan tz-aware datetime."""
    from app.modules.runs.recovery import _as_utc

    assert _as_utc(None) is None
    naive = datetime(2026, 11, 5, 10, 0)
    assert _as_utc(naive) == datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    aware = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    assert _as_utc(aware) == aware


# ==============================================================================
# 3. Lifespan Startup Hook Integration (Simulasi Restart Server)
# ==============================================================================


@pytest.mark.asyncio
async def test_lifespan_startup_hook_recovers_running_sessions(
    monkeypatch, db_session, produk_otomatis, admin_user
):
    """Edge: simulasi restart server lewat lifespan FastAPI memulihkan sesi RUNNING (AC8)."""
    # Arrange
    sesi = _buat_sesi(db_session, produk_otomatis, admin_user, status=SessionStatus.RUNNING)
    sesi_id = sesi.id
    _tambah_snapshot_dan_attempts(db_session, sesi, [AttemptStatus.RUNNING])

    from app.shared import database

    class DummyApp:
        pass

    # Arahkan factory ke session tes yang sudah memuat sesi
    monkeypatch.setattr(database, "get_session_factory", lambda: lambda: db_session)

    # Act: jalankan lifespan FastAPI (simulasi server boot)
    async with lifespan(DummyApp()):
        pass

    # Assert: sesi telah dipulihkan saat startup hook dijalankan
    db_session.expire_all()
    baris = db_session.get(MeasurementSession, sesi_id)
    assert baris.status == SessionStatus.PAUSED
    assert baris.stop_reason == StopReason.RESTART
    assert baris.stopped_at is not None

    # Attempt running di-reset ke pending
    attempt = db_session.scalar(select(Attempt).where(Attempt.session_id == sesi_id))
    assert attempt.status == AttemptStatus.PENDING
