"""Manual answer saving and overwrite history, SCRUM-231."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.runs.manual_session_rules import (
    MANUAL_STATUSES,
    ensure_answers_open,
)
from app.modules.runs.manual_session_status import ManualSessionStatusService
from app.modules.runs.models import (
    AnswerHistory,
    Attempt,
    AttemptSource,
    MeasurementSession,
    SessionType,
)
from app.modules.runs.repository import ManualSessionRepository
from app.modules.runs.schemas import (
    ManualAnswerHistoryEntry,
    ManualAnswerResponse,
    ManualAnswerSaveRequest,
)
from app.shared.exceptions import (
    ConfirmationRequiredError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


def _manual_repo(db: Session, session_id: uuid.UUID) -> ManualSessionRepository:
    repo = ManualSessionRepository(db)
    current = repo.lock_manual(session_id)

    if current is None:
        raise NotFoundError(
            "Sesi manual tidak ditemukan.",
            code="MANUAL_SESSION_NOT_FOUND",
        )

    if current not in MANUAL_STATUSES:
        raise ConflictError(
            "Status sesi manual tidak valid.",
            code="MANUAL_SESSION_STATUS_INVALID",
        )

    ensure_answers_open(current)
    return repo


def _find_attempt(
    db: Session,
    session_id: uuid.UUID,
    snapshot_item_id: uuid.UUID,
    attempt_no: int,
    *,
    lock: bool = False,
) -> Attempt:
    query = select(Attempt).where(
        Attempt.session_id == session_id,
        Attempt.snapshot_item_id == snapshot_item_id,
        Attempt.repetition_no == attempt_no,
        Attempt.source == AttemptSource.MANUAL,
    )

    if lock:
        query = query.with_for_update()

    attempt = db.scalar(query)
    if attempt is None:
        raise ValidationError(
            "Kasus atau percobaan tidak ditemukan pada sesi.",
            code="MANUAL_ATTEMPT_NOT_FOUND",
        )

    return attempt


def save_manual_answer(
    db: Session,
    session_id: uuid.UUID,
    payload: ManualAnswerSaveRequest,
    *,
    actor: uuid.UUID,
    now: datetime | None = None,
) -> ManualAnswerResponse:
    now = now or datetime.now(UTC)

    try:
        # Lock sesi dulu, kemudian attempt, untuk menghindari
        # race antara penyimpanan, overwrite, dan finalize.
        repo = _manual_repo(db, session_id)

        attempt = _find_attempt(
            db,
            session_id,
            payload.snapshot_item_id,
            payload.attempt_no,
            lock=True,
        )

        if attempt.filled_at is not None:
            if not payload.requires_confirmation:
                raise ConfirmationRequiredError(
                    "Jawaban sudah ada. Konfirmasi sebelum menimpa.",
                    affected_count=1,
                )

            db.add(
                AnswerHistory(
                    attempt_id=attempt.id,
                    answer_md=attempt.answer_text,
                    filled_by=attempt.filled_by,
                    filled_at=attempt.filled_at,
                    obtained_at=attempt.answered_at_platform,
                    platform_refused=attempt.platform_refused,
                    note=attempt.manual_note,
                    overwritten_by=actor,
                    overwritten_at=now,
                )
            )
            db.flush()

        attempt.answer_text = payload.answer_md
        attempt.platform_refused = payload.platform_refused
        attempt.manual_note = payload.note
        attempt.answered_at_platform = payload.obtained_at or now
        attempt.filled_by = actor
        attempt.filled_at = now

        db.flush()

        new_status = ManualSessionStatusService(repo).recompute(
            session_id,
            now=now,
        )

        response = ManualAnswerResponse(
            attempt_id=attempt.id,
            snapshot_item_id=attempt.snapshot_item_id,
            attempt_no=attempt.repetition_no,
            source=attempt.source,
            answer_md=attempt.answer_text,
            obtained_at=attempt.answered_at_platform,
            platform_refused=attempt.platform_refused,
            note=attempt.manual_note,
            filled_by=attempt.filled_by,
            filled_at=attempt.filled_at,
            session_status=new_status,
        )

        db.commit()
        return response

    except Exception:
        db.rollback()
        raise


def manual_answer_history(
    db: Session,
    session_id: uuid.UUID,
    snapshot_item_id: uuid.UUID,
    attempt_no: int,
) -> list[ManualAnswerHistoryEntry]:
    session = db.get(MeasurementSession, session_id)

    if session is None or session.session_type != SessionType.MANUAL:
        raise NotFoundError(
            "Sesi manual tidak ditemukan.",
            code="MANUAL_SESSION_NOT_FOUND",
        )

    attempt = _find_attempt(
        db,
        session_id,
        snapshot_item_id,
        attempt_no,
    )

    records = db.scalars(
        select(AnswerHistory)
        .where(AnswerHistory.attempt_id == attempt.id)
        .order_by(AnswerHistory.overwritten_at, AnswerHistory.id)
    ).all()

    return [
        ManualAnswerHistoryEntry.model_validate(
            record,
            from_attributes=True,
        )
        for record in records
    ]
