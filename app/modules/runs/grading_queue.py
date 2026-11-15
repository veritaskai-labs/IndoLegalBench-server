"""Penilaian di latar belakang: antre run dan baca progresnya. SCRUM-78 SCRUM-284 (K-13).

POST /grade memanggil judge di dalam request, jadi pop-up Nilai tertahan sampai semua percobaan
dinilai. Di sini request hanya mencatat run berstatus queued beserta jumlah percobaannya, lalu
membalas 202; worker penilaian yang memanggil judge dan mengisi progres.
"""

import uuid
from dataclasses import dataclass
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
from app.modules.runs.schemas import (
    GradeSessionRequest,
    GradingProgress,
    GradingRunQueued,
    GradingRunSummary,
)
from app.modules.runs.session_grade import (
    GRADING_IN_PROGRESS,
    MEASUREMENT_NOT_FOUND_MESSAGE,
    list_grading_runs,
    prepare_grading,
)
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.security import Role

_ACTIVE_RUN_INDEX = "uq_grading_runs_one_active"


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
    except IntegrityError as exc:
        db.rollback()
        if not is_active_run_conflict(exc):
            raise
        # Dua klik bersamaan lolos cek run aktif; index uq_grading_runs_one_active menolak satu.
        raise ConflictError(
            "Penilaian sesi ini masih berjalan. Tunggu sampai selesai.",
            code=GRADING_IN_PROGRESS,
        ) from None
    db.add(GradingRunProgress(grading_run_id=run.id, attempts_total=_attempt_count(db, session_id)))
    db.commit()
    return GradingRunQueued(grading_run_id=run.id, status="queued")


def is_active_run_conflict(exc: IntegrityError) -> bool:
    """True bila galat berasal dari index satu run aktif, bukan FK atau NOT NULL (review #142).

    Pencocokan memakai teks galat dari driver, jadi hanya andal untuk pesan PostgreSQL yang
    menyebut nama index uq_grading_runs_one_active (yang dipakai di dev dan prod). Cabang SQLite
    (kolom unik grading_runs.session_id) hanya untuk tes lokal; bila driver mengganti teksnya,
    galat diteruskan apa adanya, tidak salah dilaporkan sebagai 409 (review #143).
    """
    message = str(exc.orig)
    return (
        _ACTIVE_RUN_INDEX in message
        or "UNIQUE constraint failed: grading_runs.session_id" in message
    )


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
    counts = read_run_counts(db, run.id)
    return GradingProgress(
        grading_run_id=run.id,
        status=run.status.value,
        attempts_graded=counts.graded,
        attempts_total=counts.total,
        attempts_ungraded=counts.total - counts.graded,
        percent=progress_percent(
            counts.graded, counts.total, finished=run.status == GradingRunStatus.FINISHED
        ),
        error_code=counts.error_code,
        triggered_at=run.triggered_at,
        finished_at=run.finished_at,
    )


def list_grading_runs_with_progress(db: Session, session_id: uuid.UUID) -> list[GradingRunSummary]:
    """Riwayat penilaian sesi, tiap baris dengan kode gagal dan jumlah percobaannya (K-19)."""
    summaries = list_grading_runs(db, session_id)
    rows = []
    for summary in summaries:
        counts = read_run_counts(db, summary.grading_run_id)
        rows.append(
            summary.model_copy(
                update={
                    "error_code": counts.error_code,
                    "attempts_graded": counts.graded,
                    "attempts_total": counts.total,
                    "attempts_ungraded": counts.total - counts.graded,
                }
            )
        )
    return rows


@dataclass(frozen=True)
class RunCounts:
    graded: int
    total: int
    error_code: str | None


def read_run_counts(db: Session, run_id: uuid.UUID) -> RunCounts:
    """Percobaan yang sudah dinilai, totalnya, dan kode gagal satu run.

    Run dari POST /grade lama tidak punya baris progres: semua hasilnya sudah tersimpan dalam
    satu request, jadi jumlahnya dari hasil yang tersimpan dan kode gagalnya kosong.
    """
    progress = db.get(GradingRunProgress, run_id)
    if progress is None:
        graded = _result_count(db, run_id)
        return RunCounts(graded=graded, total=graded, error_code=None)
    return RunCounts(
        graded=progress.attempts_graded,
        total=progress.attempts_total,
        error_code=progress.error_code,
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
