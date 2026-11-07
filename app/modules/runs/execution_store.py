"""SessionStore tabel untuk worker. SCRUM-76 SCRUM-249.

Claim durable sebelum I/O; jawaban dan call log satu transaksi sesudahnya.
Attempt running yang terputus hanya dipulihkan SCRUM-217, bukan dicoba ulang diam-diam.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.notifications.service import TransactionNotifier
from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionProductCopy,
    SessionStatus,
    StopReason,
)
from app.modules.runs.ports import AttemptRecord, SessionStore
from app.modules.runs.recovery import record_heartbeat
from app.modules.runs.repository import SessionStatusRepository
from app.modules.runs.session_notice import notify_session_ended

_WRONG_SESSION = "wrong session"


class ExecutionInterrupted(Exception):
    """Kontrol sesi atau penghentian proses melarang dispatch berikutnya."""


class TableSessionStore(SessionStore):
    def __init__(self, db: Session, session_id: uuid.UUID, *, stopping=lambda: False):
        self.db = db
        self.session_id = session_id
        self.stopping = stopping
        self.items = list(
            db.scalars(
                select(CaseSnapshotItem)
                .join(CaseSnapshot)
                .where(CaseSnapshot.session_id == session_id)
                .order_by(CaseSnapshotItem.case_id, CaseSnapshotItem.id)
            )
        )

    def list_cases(self, session_id: uuid.UUID) -> list[str]:
        if session_id != self.session_id:
            raise ValueError(_WRONG_SESSION)
        return [item.body["sections"]["identity.question"] for item in self.items]

    def _session(self) -> MeasurementSession:
        return self.db.scalars(
            select(MeasurementSession)
            .where(MeasurementSession.id == self.session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()

    def prepare_attempt(self, case_index: int, repeat_index: int) -> uuid.UUID | None:
        if self.stopping() or self._session().status != SessionStatus.RUNNING:
            raise ExecutionInterrupted
        row = self.db.scalars(
            select(Attempt)
            .where(
                Attempt.session_id == self.session_id,
                Attempt.snapshot_item_id == self.items[case_index].id,
                Attempt.repetition_no == repeat_index + 1,
            )
            .execution_options(populate_existing=True)
        ).one()
        if row.status == AttemptStatus.RUNNING:
            raise RuntimeError("uncertain attempt requires recovery before dispatch")
        if row.status != AttemptStatus.PENDING:
            return None
        claimed = self.db.execute(
            update(Attempt)
            .where(
                Attempt.id == row.id,
                Attempt.status == AttemptStatus.PENDING,
            )
            .values(status=AttemptStatus.RUNNING, started_at=datetime.now(UTC), finished_at=None)
        ).rowcount
        self.db.commit()
        if claimed != 1:
            raise ExecutionInterrupted
        record_heartbeat(self.db, self.session_id)
        return row.id

    def save_attempt(self, record: AttemptRecord) -> None:
        if record.session_id != self.session_id or record.attempt_id is None:
            raise ValueError("attempt is not claimed")
        session = self._session()
        row = self.db.scalars(
            select(Attempt).where(
                Attempt.id == record.attempt_id,
                Attempt.session_id == self.session_id,
                Attempt.snapshot_item_id == self.items[record.case_index].id,
                Attempt.repetition_no == record.repeat_index + 1,
                Attempt.status == AttemptStatus.RUNNING,
            )
        ).one()
        failed = record.failure_kind is not None or record.error is not None
        row.status = AttemptStatus.TECHNICAL_FAILURE if failed else AttemptStatus.SUCCEEDED
        # Partial failed answers belong to the call evidence; successful answers are immutable.
        row.answer_text = None if failed else record.text
        row.model_version = record.model_version
        # The copy trigger reads the attempt as evidence; persist it before queuing that update.
        self.db.flush()
        copy = self.db.scalar(
            select(SessionProductCopy).where(SessionProductCopy.session_id == self.session_id)
        )
        self._verify_resumed_response(session, copy.model_version_at_start, record.model_version)
        if copy.model_version_at_start is None and record.model_version:
            copy.model_version_at_start = record.model_version
        self.db.flush()

    def _verify_resumed_response(self, session, baseline, observed):
        if not session.resume_version_check_pending:
            return
        if not observed:
            session.model_version_unverified = True
            return
        session.resume_version_check_pending = False
        if baseline and observed != baseline:
            # The HTTP 202 is already sent; persist the conflict for GET and future resume.
            session.resume_error_code = "MODEL_VERSION_CHANGED"
            if session.status == SessionStatus.RUNNING:
                SessionStatusRepository(self.db).change_status(
                    self.session_id,
                    SessionStatus.PAUSED,
                    at=datetime.now(UTC),
                    stop_reason=StopReason.ERROR,
                    commit=False,
                )

    def update_progress(self, session_id: uuid.UUID, *, completed: int, failed: int) -> None:
        # Counters are derived from attempts by the list/progress APIs, never duplicated here.
        if session_id != self.session_id:
            raise ValueError(_WRONG_SESSION)
        record_heartbeat(self.db, session_id)

    def commit_attempt(self) -> None:
        self.db.commit()

    def mark_status(self, session_id, status, *, stop_reason=None) -> None:
        row = self._session()
        if session_id != self.session_id:
            raise ValueError(_WRONG_SESSION)
        if row.status != SessionStatus.RUNNING:
            return  # A control action wins over worker completion.
        if status in (SessionStatus.FINISHED, SessionStatus.FINISHED_WITH_FAILURES):
            statuses = list(
                self.db.scalars(select(Attempt.status).where(Attempt.session_id == session_id))
            )
            if any(value in (AttemptStatus.PENDING, AttemptStatus.RUNNING) for value in statuses):
                raise RuntimeError("unfinished attempts require recovery")
            status = (
                SessionStatus.FINISHED_WITH_FAILURES
                if AttemptStatus.TECHNICAL_FAILURE in statuses
                else SessionStatus.FINISHED
            )
        try:
            SessionStatusRepository(self.db).change_status(
                session_id, status, at=datetime.now(UTC), stop_reason=stop_reason, commit=False
            )
            if status != SessionStatus.PAUSED:
                notify_session_ended(
                    TransactionNotifier(self.db),
                    session_id=session_id,
                    label=row.label,
                    status=status,
                    started_by=row.created_by,
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
