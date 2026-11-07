"""Status sesi manual: hitung ulang dan Tandai selesai. PBI-22 (SCRUM-176) AC8, AC13; SCRUM-230.

Aturannya ada di manual_session_rules (murni). Di sini aturan itu dipasang ke tabel:
baris sesi dikunci dulu, lalu status ditulis lewat SessionStatusRepository.change_status,
satu-satunya jalan tulis status yang mengikuti tabel transisi dan mencatat audit (PBI-18).
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.manual_session_rules import (
    MANUAL_STATUSES,
    ManualProgress,
    answer_date_range,
    ensure_finalizable,
    filling_status,
)
from app.modules.runs.models import Attempt, MeasurementSession, SessionStatus, SessionType
from app.modules.runs.schemas import ManualFinalizeResponse
from app.shared.exceptions import ConflictError, NotFoundError


def _manual_for_update(db: Session, session_id: uuid.UUID) -> MeasurementSession:
    session = repository.get_session_for_update(db, session_id)
    if session is None or session.session_type != SessionType.MANUAL:
        raise NotFoundError(
            "Sesi pengukuran manual tidak ditemukan.", code="MANUAL_SESSION_NOT_FOUND"
        )
    return session


def recompute_manual_status(db: Session, session_id: uuid.UUID, *, now: datetime) -> SessionStatus:
    """Status D8 dan rentang tanggal setelah jawaban disimpan. Dipanggil SCRUM-231.

    Tidak commit: jawaban dan statusnya tersimpan atau batal bersama di transaksi
    pemanggil. Baris sesi dikunci, jadi dua pengisi bersamaan tidak saling menimpa.
    """
    session = _manual_for_update(db, session_id)
    current = session.status
    if current not in MANUAL_STATUSES:
        # Baris manual lama dengan status D4 (sebelum SCRUM-230): 409, bukan 500 (review #116).
        raise ConflictError(
            "Sesi manual ini masih berstatus sesi otomatis dan tidak bisa diisi.",
            code="MANUAL_SESSION_STATUS_INVALID",
        )
    milik_sesi = Attempt.session_id == session_id
    total, filled = db.execute(
        select(func.count(), func.count(Attempt.filled_at)).where(milik_sesi)
    ).one()
    target = filling_status(current, ManualProgress(filled=filled, total=total))
    # AC13: rentang dari jawaban yang terisi saja.
    dari, sampai = answer_date_range(
        db.scalars(
            select(Attempt.answered_at_platform).where(milik_sesi, Attempt.filled_at.is_not(None))
        )
    )
    db.execute(
        update(MeasurementSession)
        .where(MeasurementSession.id == session_id)
        .values(answer_date_from=dari, answer_date_to=sampai)
        .execution_options(synchronize_session=False)
    )
    if target != current:
        repository.SessionStatusRepository(db).change_status(
            session_id, target, at=now, commit=False
        )
    db.flush()
    return target


def finalize_manual_session(
    db: Session, session_id: uuid.UUID, *, finalized_by: uuid.UUID, now: datetime
) -> ManualFinalizeResponse:
    """Tandai selesai: hanya dari Lengkap atau Dinilai, sekali, dengan pelakunya (AC8, D9)."""
    session = _manual_for_update(db, session_id)
    ensure_finalizable(session.status)
    repository.SessionStatusRepository(db).change_status(
        session_id, SessionStatus.FINALIZED, at=now, finalized_by=finalized_by
    )
    return ManualFinalizeResponse(
        id=session_id,
        status=SessionStatus.FINALIZED,
        finalized_at=now,
        finalized_by=finalized_by,
    )
