"""Pemulihan sesi setelah restart server dan pemantauan liveness worker.

PBI-12 AC8, D5 langkah 7 (SCRUM-217).

Tujuan:
1. Saat server menyala kembali (restart / deploy), sesi otomatis yang berstatus
   Berjalan (RUNNING) dan worker-nya sudah mati ditandai Dihentikan (PAUSED) dengan
   stop_reason = RESTART.
2. Tidak lanjut otomatis: Admin menekan 'Lanjutkan' (AC8).
3. Attempt yang sedang berjalan saat proses mati ditandai TECHNICAL_FAILURE (SCRUM-268,
   opsi A). Tidak di-reset ke pending: panggilan itu mungkin sudah ditagih provider,
   jadi hanya Coba ulang yang dipilih Admin yang boleh mengirimnya lagi. Sesi lalu
   selesai dengan kegagalan lewat alur Lanjutkan yang sudah ada.
4. Worker liveness: sesi Berjalan yang heartbeat-nya lewat batas waktu (stale)
   ditandai Dihentikan lewat layanan transisi (SessionStatusRepository).
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    MeasurementSession,
    SessionStatus,
    SessionType,
    StopReason,
)
from app.modules.runs.repository import SessionStatusRepository

logger = logging.getLogger(__name__)

# Default timeout heartbeat: 300 detik (5 menit).
# Batas waktu satu panggilan ke penyedia AI adalah 240 detik (ASK_TIMEOUT_SECONDS),
# sehingga batas liveness harus lebih besar dari 240 detik agar panggilan
# yang lambat tetapi sah tidak dianggap mati prematurely.
DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS = 300.0


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def fail_interrupted_attempts(db: Session, session_id: uuid.UUID, *, at: datetime) -> int:
    """Tandai attempt RUNNING milik sesi ini sebagai gagal teknis, tanpa commit.

    Hanya dipanggil saat tidak ada dispatcher yang memegang lease, jadi attempt RUNNING
    pasti yatim: panggilannya terputus. Baris call_logs tidak ditambah karena biaya
    panggilan yang terputus tidak diketahui. Idempoten: baris yang sudah gagal tidak
    disentuh lagi. finished_at hanya diisi bila started_at ada (CHECK finished_needs_started).
    """
    return db.execute(
        update(Attempt)
        .where(Attempt.session_id == session_id, Attempt.status == AttemptStatus.RUNNING)
        .values(
            status=AttemptStatus.TECHNICAL_FAILURE,
            finished_at=case((Attempt.started_at.is_not(None), at), else_=Attempt.finished_at),
        )
    ).rowcount


class RecoveryService:
    """Layanan pemulihan sesi saat restart dan pemantauan heartbeat worker."""

    def __init__(
        self,
        db: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        status_repository: SessionStatusRepository | None = None,
    ) -> None:
        self._db = db
        self._clock = clock or (lambda: datetime.now(UTC))
        self._status_repo = status_repository or SessionStatusRepository(db)

    def recover_after_restart(self) -> list[uuid.UUID]:
        """Tandai seluruh sesi otomatis yang RUNNING menjadi PAUSED saat startup.

        Sesuai D5 langkah 7 (nomor 39-41) dan MEASUREMENT_WORKER.md:
        - SELECT sesi status = running (dan session_type = automatic)
        - UPDATE status = paused, stop_reason = 'restart', stopped_at = now
        - Attempt in-flight ditandai TECHNICAL_FAILURE dalam transaksi yang sama (SCRUM-268).
        - Tidak lanjut otomatis; Admin menekan Lanjutkan.
        """
        now = self._clock()
        running_sessions = list(
            self._db.scalars(
                select(MeasurementSession.id).where(
                    MeasurementSession.status == SessionStatus.RUNNING,
                    MeasurementSession.session_type == SessionType.AUTOMATIC,
                )
            ).all()
        )
        if not running_sessions:
            return []

        recovered_ids: list[uuid.UUID] = []
        for session_id in running_sessions:
            try:
                fail_interrupted_attempts(self._db, session_id, at=now)
                self._status_repo.change_status(
                    session_id,
                    SessionStatus.PAUSED,
                    at=now,
                    stop_reason=StopReason.RESTART,
                    commit=True,
                )
                recovered_ids.append(session_id)
            except Exception:
                self._db.rollback()
                logger.exception("Failed to recover session %s on restart", session_id)

        return recovered_ids

    def check_worker_liveness(
        self,
        *,
        timeout_seconds: float = DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS,
    ) -> list[uuid.UUID]:
        """Periksa heartbeat worker pada sesi otomatis yang sedang RUNNING.

        Bila updated_at sesi lebih lampau dari now - timeout_seconds,
        worker dianggap mati/hang dan sesi ditandai PAUSED (stop_reason = RESTART).
        """
        now = self._clock()
        cutoff = now - timedelta(seconds=timeout_seconds)

        candidates = list(
            self._db.scalars(
                select(MeasurementSession).where(
                    MeasurementSession.status == SessionStatus.RUNNING,
                    MeasurementSession.session_type == SessionType.AUTOMATIC,
                )
            ).all()
        )

        stale_ids: list[uuid.UUID] = []
        for session in candidates:
            session_updated_at = _as_utc(session.updated_at)
            if session_updated_at is not None and session_updated_at <= cutoff:
                try:
                    self._status_repo.change_status(
                        session.id,
                        SessionStatus.PAUSED,
                        at=now,
                        stop_reason=StopReason.RESTART,
                        commit=True,
                    )
                    stale_ids.append(session.id)
                except Exception:
                    self._db.rollback()
                    logger.exception(
                        "Failed to pause stale session %s on liveness check", session.id
                    )

        return stale_ids

    def record_heartbeat(self, session_id: uuid.UUID) -> bool:
        """Perbarui timestamp heartbeat (updated_at) sesi yang sedang RUNNING."""
        now = self._clock()
        updated = self._db.execute(
            update(MeasurementSession)
            .where(
                MeasurementSession.id == session_id,
                MeasurementSession.status == SessionStatus.RUNNING,
            )
            .values(updated_at=now)
        ).rowcount
        if updated == 1:
            self._db.commit()
            return True
        return False


def recover_after_restart(db: Session, *, at: datetime | None = None) -> list[uuid.UUID]:
    """Helper fungsi untuk pemulihan startup server restart/deploy."""
    clock = (lambda: at) if at is not None else None
    return RecoveryService(db, clock=clock).recover_after_restart()


def check_worker_liveness(
    db: Session,
    *,
    timeout_seconds: float = DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS,
    at: datetime | None = None,
) -> list[uuid.UUID]:
    """Helper fungsi untuk pemeriksaan liveness/heartbeat worker."""
    clock = (lambda: at) if at is not None else None
    return RecoveryService(db, clock=clock).check_worker_liveness(timeout_seconds=timeout_seconds)


def record_heartbeat(db: Session, session_id: uuid.UUID, *, at: datetime | None = None) -> bool:
    """Helper fungsi untuk mencatat heartbeat."""
    clock = (lambda: at) if at is not None else None
    return RecoveryService(db, clock=clock).record_heartbeat(session_id)
