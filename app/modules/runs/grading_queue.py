"""Penilaian di latar belakang: antre run dan baca progresnya. SCRUM-78 SCRUM-284 (K-13).

POST /grade memanggil judge di dalam request, jadi pop-up Nilai tertahan sampai semua percobaan
dinilai. Di sini request hanya mencatat run berstatus queued beserta jumlah percobaannya, lalu
membalas 202; worker penilaian yang memanggil judge dan mengisi progres.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunKind,
    GradingRunProgress,
    GradingRunStatus,
    RubricVersion,
)
from app.modules.runs.models import Attempt, MeasurementSession
from app.modules.runs.schemas import GradeSessionRequest, GradingProgress, GradingRunQueued
from app.modules.runs.session_grade import (
    GRADING_IN_PROGRESS,
    MEASUREMENT_NOT_FOUND_MESSAGE,
    prepare_grading,
)
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.security import Role


def enqueue_grading(
    db: Session,
    session_id: uuid.UUID,
    payload: GradeSessionRequest | None,
    *,
    actor_id: uuid.UUID,
    actor_role: Role,
    now: datetime | None = None,
) -> GradingRunQueued:
    """Catat run queued untuk worker penilaian; aturan sama dengan POST /grade."""
    prepared = prepare_grading(db, session_id, payload, actor_role=actor_role)
    has_earlier = db.scalar(
        select(select(GradingRun.id).where(GradingRun.session_id == session_id).exists())
    )
    run = GradingRun(
        session_id=session_id,
        rubric_version_id=_rubric_version_id(db, prepared.rubric_version),
        judge_provider_id=prepared.judge_id,
        kind=GradingRunKind.REGRADE if has_earlier else GradingRunKind.INITIAL,
        status=GradingRunStatus.QUEUED,
        triggered_by=actor_id,
        triggered_at=now or datetime.now(UTC),
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # Dua klik bersamaan lolos cek run aktif; index uq_grading_runs_one_active menolak satu.
        db.rollback()
        raise ConflictError(
            "Penilaian sesi ini masih berjalan. Tunggu sampai selesai.",
            code=GRADING_IN_PROGRESS,
        ) from None
    db.add(GradingRunProgress(grading_run_id=run.id, attempts_total=_attempt_count(db, session_id)))
    db.commit()
    return GradingRunQueued(grading_run_id=run.id, status="queued")


def get_grading_progress(db: Session, session_id: uuid.UUID) -> GradingProgress | None:
    """Progres run terbaru sesi ini, atau None bila sesi belum pernah dinilai."""
    if db.get(MeasurementSession, session_id) is None:
        raise NotFoundError(MEASUREMENT_NOT_FOUND_MESSAGE, code="MEASUREMENT_NOT_FOUND")
    run = db.scalars(
        select(GradingRun)
        .where(GradingRun.session_id == session_id)
        .order_by(GradingRun.triggered_at.desc(), GradingRun.id.desc())
        .limit(1)
    ).first()
    if run is None:
        return None
    progress = db.get(GradingRunProgress, run.id)
    if progress is None:
        # Run dari POST /grade selesai dalam satu request: semua hasilnya sudah tersimpan.
        graded = _result_count(db, run.id)
        total, error_code = graded, None
    else:
        graded, total, error_code = (
            progress.attempts_graded,
            progress.attempts_total,
            progress.error_code,
        )
    return GradingProgress(
        grading_run_id=run.id,
        status=run.status.value,
        attempts_graded=graded,
        attempts_total=total,
        percent=progress_percent(graded, total, finished=run.status == GradingRunStatus.FINISHED),
        error_code=error_code,
        triggered_at=run.triggered_at,
        finished_at=run.finished_at,
    )


def progress_percent(graded: int, total: int, *, finished: bool) -> int:
    """Persen dibulatkan ke bawah, jadi 100 hanya untuk run yang sudah selesai.

    Sesi tanpa percobaan tidak punya pembagi: 100 bila selesai, 0 selama belum.
    """
    if total == 0:
        return 100 if finished else 0
    return min(100, graded * 100 // total)


def _attempt_count(db: Session, session_id: uuid.UUID) -> int:
    return (
        db.scalar(select(func.count()).select_from(Attempt).where(Attempt.session_id == session_id))
        or 0
    )


def _result_count(db: Session, run_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(GradingResult)
            .where(GradingResult.grading_run_id == run_id)
        )
        or 0
    )


def _rubric_version_id(db: Session, version: str) -> uuid.UUID:
    """Versi rubrik "vN" sudah divalidasi prepare_grading, jadi barisnya pasti ada."""
    return db.scalar(
        select(RubricVersion.id).where(RubricVersion.version_no == int(version.removeprefix("v")))
    )
