"""Grading reads and publication of completed grading runs."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunKind,
    GradingRunStatus,
    JudgeKind,
    JudgeStatus,
    LlmJudgeResult,
    RubricVersion,
)
from app.modules.runs.models import Attempt, MeasurementSession


@dataclass(frozen=True)
class LatestGradeScores:
    run_id: uuid.UUID | None
    rubric_version_id: uuid.UUID | None
    deterministic: dict[uuid.UUID, Decimal | None]
    quality: dict[uuid.UUID, Decimal | None]
    format: dict[uuid.UUID, Decimal | None]


@dataclass(frozen=True)
class RunHistoryItem:
    run_id: uuid.UUID
    rubric_version_no: int
    kind: GradingRunKind
    status: GradingRunStatus
    is_latest: bool
    triggered_at: datetime


def read_latest_grades(
    db: Session,
    session_id: uuid.UUID,
) -> LatestGradeScores:
    """Read grades from the latest finished run only."""
    run = db.scalar(
        select(GradingRun).where(
            GradingRun.session_id == session_id,
            GradingRun.is_latest.is_(True),
            GradingRun.status == GradingRunStatus.FINISHED,
        )
    )

    if run is None:
        return LatestGradeScores(None, None, {}, {}, {})

    result_rows = db.execute(
        select(
            GradingResult.id,
            GradingResult.attempt_id,
            GradingResult.det_score,
        )
        .where(GradingResult.grading_run_id == run.id)
        .order_by(GradingResult.id)
    ).all()

    result_attempts = {row.id: row.attempt_id for row in result_rows}

    deterministic = {row.attempt_id: row.det_score for row in result_rows}
    quality: dict[uuid.UUID, Decimal | None] = {}
    format_scores: dict[uuid.UUID, Decimal | None] = {}

    if result_attempts:
        judge_rows = db.execute(
            select(
                LlmJudgeResult.grading_result_id,
                LlmJudgeResult.kind,
                LlmJudgeResult.criteria_score,
            )
            .where(
                LlmJudgeResult.grading_result_id.in_(result_attempts),
                LlmJudgeResult.status == JudgeStatus.SUCCEEDED,
            )
            .order_by(
                LlmJudgeResult.judge_run_no,
                LlmJudgeResult.created_at,
                LlmJudgeResult.id,
            )
        ).all()

        for row in judge_rows:
            attempt_id = result_attempts[row.grading_result_id]

            if row.kind == JudgeKind.QUALITY:
                quality[attempt_id] = row.criteria_score
            elif row.kind == JudgeKind.FORMAT:
                format_scores[attempt_id] = row.criteria_score

    return LatestGradeScores(
        run_id=run.id,
        rubric_version_id=run.rubric_version_id,
        deterministic=deterministic,
        quality=quality,
        format=format_scores,
    )


def active_rubric_version_no(db: Session) -> int:
    """Return the active rubric number rather than a hard-coded version."""
    version = db.scalar(select(RubricVersion.version_no).where(RubricVersion.is_active.is_(True)))
    if version is None:
        raise ValueError("no active rubric version")
    return version


def read_run_history(
    db: Session,
    session_id: uuid.UUID,
) -> tuple[RunHistoryItem, ...]:
    """Retain all runs and their original rubric versions."""
    rows = db.execute(
        select(GradingRun, RubricVersion.version_no)
        .join(
            RubricVersion,
            RubricVersion.id == GradingRun.rubric_version_id,
        )
        .where(GradingRun.session_id == session_id)
        .order_by(
            GradingRun.triggered_at,
            GradingRun.kind,
            GradingRun.id,
        )
    ).all()

    return tuple(
        RunHistoryItem(
            run_id=run.id,
            rubric_version_no=version_no,
            kind=run.kind,
            status=run.status,
            is_latest=run.is_latest,
            triggered_at=run.triggered_at,
        )
        for run, version_no in rows
    )


def publish_latest_run(
    db: Session,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Promote a completed run without deleting historical results.

    The caller owns the surrounding transaction and commit.
    """
    session_exists = db.scalar(
        select(MeasurementSession.id).where(MeasurementSession.id == session_id).with_for_update()
    )
    if session_exists is None:
        raise ValueError("session not found")

    target = db.scalar(
        select(GradingRun)
        .where(
            GradingRun.id == run_id,
            GradingRun.session_id == session_id,
        )
        .with_for_update()
    )

    if target is None:
        raise ValueError("grading run does not belong to session")

    if target.status != GradingRunStatus.FINISHED:
        raise ValueError("only a finished grading run can become latest")

    if target.kind == GradingRunKind.REGRADE:
        rubric = db.get(RubricVersion, target.rubric_version_id)
        if rubric is None or not rubric.is_active:
            raise ValueError("regrade must use the active rubric")

    expected = db.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.session_id == session_id)
    )

    actual = db.scalar(
        select(func.count())
        .select_from(GradingResult)
        .where(GradingResult.grading_run_id == run_id)
    )

    if not expected or actual != expected:
        raise ValueError("incomplete grading run")

    if target.is_latest:
        return

    previous = db.scalars(
        select(GradingRun)
        .where(
            GradingRun.session_id == session_id,
            GradingRun.is_latest.is_(True),
        )
        .with_for_update()
    ).all()

    for run in previous:
        run.is_latest = False

    # Clear the old unique-index entry first.
    db.flush()

    target.is_latest = True
    db.flush()
