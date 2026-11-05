"""Terima apa adanya: flag, bukan status. PBI-12 AC9, SCRUM-214.

Sesi finished_with_failures dianggap selesai sepenuhnya bila Admin menandainya
dengan alasan tertulis; pelaku dan waktunya ikut tersimpan. Admin boleh menghapus
tanda itu lalu menandai ulang (keputusan Rafa 4 Nov). Selama tertanda, sesi tidak
bisa dicoba ulang (SessionStatusRepository.change_status, dan trigger database).

Urutan pemeriksaan: sesi ada (404), alasan terisi (422), lalu keadaan sesi (409).
Baris dikunci sampai commit supaya dua Admin bersamaan tidak saling menimpa.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.models import MeasurementSession, SessionStatus
from app.modules.runs.schemas import SessionAcceptResponse
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.text import strip_blank


def _sesi(db: Session, session_id: uuid.UUID) -> MeasurementSession:
    session = repository.get_session_for_update(db, session_id)
    if session is None:
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")
    return session


def _respons(session: MeasurementSession) -> SessionAcceptResponse:
    return SessionAcceptResponse(
        id=session.id,
        accepted_as_is_at=session.accepted_as_is_at,
        accepted_as_is_by=session.accepted_as_is_by,
        accepted_as_is_reason=session.accepted_as_is_reason,
    )


def accept_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    reason: str | None,
    accepted_by: uuid.UUID,
    now: datetime,
) -> SessionAcceptResponse:
    session = _sesi(db, session_id)
    # strip_blank, bukan strip(): zero-width space dan BOM juga bukan alasan (catatan Adra #89).
    alasan = strip_blank(reason or "")
    if not alasan:
        raise ValidationError(
            "Alasan menerima sesi apa adanya wajib diisi.", code="REASON_REQUIRED", field="reason"
        )
    if session.status != SessionStatus.FINISHED_WITH_FAILURES:
        raise ConflictError(
            "Hanya sesi Selesai dengan kegagalan yang bisa diterima apa adanya.",
            code="SESSION_NOT_ACCEPTABLE",
        )
    if session.accepted_as_is_at is not None:
        raise ConflictError(
            "Sesi sudah diterima apa adanya. Hapus tandanya dulu untuk mengganti alasan.",
            code="ALREADY_ACCEPTED",
        )
    session.accepted_as_is_at = now
    session.accepted_as_is_by = accepted_by
    session.accepted_as_is_reason = alasan
    db.commit()
    return _respons(session)


def clear_acceptance(db: Session, session_id: uuid.UUID) -> SessionAcceptResponse:
    session = _sesi(db, session_id)
    if session.accepted_as_is_at is None:
        raise ConflictError("Sesi belum diterima apa adanya.", code="NOT_ACCEPTED")
    session.accepted_as_is_at = None
    session.accepted_as_is_by = None
    session.accepted_as_is_reason = None
    db.commit()
    return _respons(session)
