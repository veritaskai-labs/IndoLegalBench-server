"""Kembalikan sesi pengukuran dari arsip ke daftar utama. PBI-11 AC10, SCRUM-263.

Unarchive hanya mengosongkan archived_at dan archived_by. Status dan datanya tidak
berubah. Catatan audit ditulis listener audit dari perubahan archived_at
(MeasurementSessionTracker), sama seperti arsip.

Aturan siapa yang boleh sama dengan arsip: Admin sesi siapa pun, Developer AI hanya
sesi yang ia buat (D10 baris kontrol sesi).
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.schemas import SessionUnarchiveResponse
from app.modules.runs.session_archive import NOT_YOUR_SESSION
from app.modules.runs.session_rules import ensure_unarchivable
from app.shared.exceptions import ForbiddenError, NotFoundError
from app.shared.security import Role


def unarchive_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    actor_role: Role,
) -> SessionUnarchiveResponse:
    session = repository.get_session_for_update(db, session_id)
    if session is None:
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")
    if actor_role == Role.DEVELOPER_AI and session.created_by != actor_id:
        raise ForbiddenError(
            "Developer AI hanya bisa mengembalikan sesi yang ia buat.", code=NOT_YOUR_SESSION
        )
    ensure_unarchivable(archived_at=session.archived_at)
    session.archived_at = None
    session.archived_by = None
    db.commit()
    return SessionUnarchiveResponse(id=session.id, status=session.status)
