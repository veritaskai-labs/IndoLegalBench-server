"""Kontrol berhenti seluruh sesi, melalui transisi tunggal SCRUM-214."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.models import AttemptStatus, SessionStatus, SessionType, StopReason
from app.modules.runs.schemas import SessionControlResponse, SessionResumeResponse
from app.modules.runs.session_control_repository import (
    SessionControlRepository,
    is_active_slot_conflict,
)
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.shared.security import CurrentUser, Role


def _controlled_session(db, session_id, actor):
    row = repository.get_session_for_update(db, session_id)
    if row is None:
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")
    if actor is not None and actor.role == Role.DEVELOPER_AI and row.created_by != actor.user_id:
        raise ForbiddenError(
            "Developer AI hanya mengontrol sesi miliknya.", code="NOT_YOUR_SESSION"
        )
    if row.archived_at is not None:
        raise ConflictError("Sesi sudah diarsipkan.", code="SESSION_ARCHIVED")
    if row.session_type != SessionType.AUTOMATIC:
        raise ConflictError("Kontrol ini untuk sesi otomatis.", code="SESSION_NOT_AUTOMATIC")
    if row.accepted_as_is_at is not None:
        raise ConflictError("Sesi sudah diterima apa adanya.", code="SESSION_ACCEPTED")
    return row


def stop_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    target: SessionStatus,
    now: datetime,
    actor: CurrentUser | None = None,
) -> SessionControlResponse:
    """Blok dispatch berikutnya; call yang sudah diklaim tetap disimpan worker.

    Status HTTP 202 berarti call aktif bisa masih berjalan. Tidak ada pembatalan
    jaringan atau perubahan attempt/log. Penolakan berulang mengikuti D4 (409).
    """
    try:
        _controlled_session(db, session_id, actor)
        repository.SessionStatusRepository(db).change_status(
            session_id, target, at=now, stop_reason=StopReason.USER, commit=False
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return SessionControlResponse(
        id=session_id, status=target, stopped_at=now, stop_reason=StopReason.USER
    )


def _long_stop(stopped_at, now, days):
    if stopped_at is None:
        return False  # Legacy stop without evidence cannot establish elapsed time.
    stopped_at = stopped_at.replace(tzinfo=UTC) if stopped_at.tzinfo is None else stopped_at
    return now - stopped_at > timedelta(days=days)


def _dispatcher_is_active(db):
    # An orphan running attempt is recovery's responsibility; SQLite has no advisory lease.
    if db.get_bind().dialect.name != "postgresql":
        return False
    from app.modules.runs.execution import worker_lease

    with worker_lease(db.get_bind()) as acquired:
        return not acquired


def _check_version(row, copy, reads, actor, confirm_unverified_version):
    if row.resume_error_code is not None:
        raise ConflictError("Versi model berubah. Mulai sesi baru.", code="MODEL_VERSION_CHANGED")
    latest = reads.latest_success(row.product_id)
    baseline = None if copy is None else copy.model_version_at_start
    current = None if latest is None else latest.model_version.strip() or None
    if baseline and current and baseline != current:
        raise ConflictError("Versi model berubah. Mulai sesi baru.", code="MODEL_VERSION_CHANGED")
    if latest is not None and not reads.successful_version_seen(row.product_id):
        if not confirm_unverified_version:
            raise ConflictError(
                "Provider belum mengirim versi. Konfirmasi Admin diperlukan.",
                code="MODEL_VERSION_CONFIRMATION_REQUIRED",
            )
        if actor.role != Role.ADMIN:
            raise ForbiddenError(
                "Konfirmasi versi hanya oleh Admin.", code="ADMIN_CONFIRMATION_REQUIRED"
            )
    return not baseline or current is None


def resume_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor: CurrentUser,
    now: datetime,
    long_stop_days: int,
    confirm_unverified_version: bool = False,
) -> SessionResumeResponse:
    """Reopen atomically; the existing leased dispatcher executes only pending work."""
    try:
        row = _controlled_session(db, session_id, actor)
        if row.status not in (SessionStatus.PAUSED, SessionStatus.CANCELLED):
            raise ConflictError("Sesi bukan dijeda/dibatalkan.", code="SESSION_NOT_RESUMABLE")
        reads = SessionControlRepository(db)
        copy = reads.product_copy(session_id)
        unverified = _check_version(row, copy, reads, actor, confirm_unverified_version)
        states = reads.attempt_states(session_id)
        if AttemptStatus.RUNNING in states and not _dispatcher_is_active(db):
            raise ConflictError("Percobaan belum direkonsiliasi.", code="ATTEMPT_RECOVERY_REQUIRED")
        # A never-started cancellation needs the queue's original snapshot/preparation path.
        target = SessionStatus.QUEUED if copy is None else SessionStatus.RUNNING
        if target == SessionStatus.RUNNING and reads.another_running(session_id):
            raise ConflictError(
                "Sesi otomatis lain masih berjalan.", code="ANOTHER_SESSION_RUNNING"
            )
        row.resumed_after_long_pause = row.resumed_after_long_pause or _long_stop(
            row.stopped_at, now, long_stop_days
        )
        row.model_version_unverified = row.model_version_unverified or unverified
        row.resume_version_check_pending = (
            any(state in (AttemptStatus.PENDING, AttemptStatus.RUNNING) for state in states)
            or copy is None
        )
        repository.SessionStatusRepository(db).change_status(
            session_id, target, at=now, commit=False
        )
        db.commit()
        return SessionResumeResponse(
            id=session_id,
            status=target,
            resumed_after_long_pause=row.resumed_after_long_pause,
            model_version_unverified=row.model_version_unverified,
        )
    except IntegrityError as error:
        db.rollback()
        if is_active_slot_conflict(error):
            raise ConflictError(
                "Sesi otomatis lain masih berjalan.", code="ANOTHER_SESSION_RUNNING"
            ) from None
        raise
    except Exception:
        db.rollback()
        raise
