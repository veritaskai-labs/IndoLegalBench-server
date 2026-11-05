"""Queries for control/resume; status writes still belong to SCRUM-214."""

import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CallLog,
    MeasurementSession,
    SessionProductCopy,
    SessionStatus,
    SessionType,
)


class SessionControlRepository:
    def __init__(self, db: Session):
        self.db = db

    def product_copy(self, session_id: uuid.UUID) -> SessionProductCopy | None:
        return self.db.scalar(
            select(SessionProductCopy).where(SessionProductCopy.session_id == session_id)
        )

    def latest_success(self, product_id: uuid.UUID) -> CallLog | None:
        return self.db.scalar(
            select(CallLog)
            .join(Attempt, Attempt.id == CallLog.attempt_id)
            .where(
                CallLog.product_id == product_id,
                CallLog.result == "ok",
                Attempt.status == AttemptStatus.SUCCEEDED,
            )
            .order_by(CallLog.ended_at.desc(), CallLog.started_at.desc(), CallLog.id.desc())
            .limit(1)
        )

    def successful_version_seen(self, product_id: uuid.UUID) -> bool:
        return bool(
            self.db.scalar(
                select(
                    select(CallLog.id)
                    .join(Attempt, Attempt.id == CallLog.attempt_id)
                    .where(
                        CallLog.product_id == product_id,
                        CallLog.result == "ok",
                        Attempt.status == AttemptStatus.SUCCEEDED,
                        func.trim(CallLog.model_version) != "",
                    )
                    .exists()
                )
            )
        )

    def attempt_states(self, session_id: uuid.UUID) -> list[AttemptStatus]:
        return list(self.db.scalars(select(Attempt.status).where(Attempt.session_id == session_id)))

    def another_running(self, session_id: uuid.UUID) -> bool:
        return bool(
            self.db.scalar(
                select(
                    exists().where(
                        MeasurementSession.id != session_id,
                        MeasurementSession.session_type == SessionType.AUTOMATIC,
                        MeasurementSession.status == SessionStatus.RUNNING,
                    )
                )
            )
        )


def is_active_slot_conflict(error: IntegrityError) -> bool:
    original = error.orig
    constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
    if getattr(original, "sqlstate", None) == "23505":
        return constraint == "uq_measurement_sessions_one_running_automatic"
    return (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        and str(original) == "UNIQUE constraint failed: measurement_sessions.session_type"
    )
