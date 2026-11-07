"""Grading reads and publication of completed grading runs."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
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
    """Read the latest finished grading run."""

    run = db.scalar(
        select(GradingRun).where(
            GradingRun.session_id == session_id,
            GradingRun.is_latest.is_(True),
            GradingRun.status == GradingRunStatus.FINISHED,
        )
    )

    if run is None:
        return LatestGradeScores(None, None, {}, {}, {})

    rows = db.execute(
        select(
            GradingResult.id,
            GradingResult.attempt_id,
            GradingResult.det_score,
        )
        .where(GradingResult.grading_run_id == run.id)
        .order_by(GradingResult.id)
    ).all()

    result_attempts = {row.id: row.attempt_id for row in rows}

    deterministic = {row.attempt_id: row.det_score for row in rows}

    grouped: dict[tuple[uuid.UUID, JudgeKind], list[Decimal]] = defaultdict(list)

    if result_attempts:
        judge_rows = db.execute(
            select(
                LlmJudgeResult.grading_result_id,
                LlmJudgeResult.kind,
                LlmJudgeResult.criteria_score,
            ).where(
                LlmJudgeResult.grading_result_id.in_(result_attempts),
                LlmJudgeResult.status == JudgeStatus.SUCCEEDED,
            )
        ).all()

        for row in judge_rows:
            if row.criteria_score is None:
                continue

            attempt_id = result_attempts[row.grading_result_id]
            key = (attempt_id, row.kind)
            grouped[key].append(row.criteria_score)

    def average(scores: list[Decimal]) -> Decimal:
        return (sum(scores, Decimal(0)) / Decimal(len(scores))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    quality = {
        attempt_id: average(scores)
        for (attempt_id, kind), scores in grouped.items()
        if kind == JudgeKind.QUALITY
    }

    format_scores = {
        attempt_id: average(scores)
        for (attempt_id, kind), scores in grouped.items()
        if kind == JudgeKind.FORMAT
    }

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


def assert_run_complete(
    db: Session,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Ensure every attempt has complete grading results."""

    session = db.get(MeasurementSession, session_id)

    if session is None:
        raise ValueError("session not found")

    attempts = set(db.scalars(select(Attempt.id).where(Attempt.session_id == session_id)))

    results = db.scalars(select(GradingResult).where(GradingResult.grading_run_id == run_id)).all()

    if not attempts or len(results) != len(attempts):
        raise ValueError("incomplete grading run: missing attempt results")

    if {row.attempt_id for row in results} != attempts:
        raise ValueError("incomplete grading run: attempts do not match session")

    result_ids = {row.id for row in results}

    judges = db.scalars(
        select(LlmJudgeResult).where(LlmJudgeResult.grading_result_id.in_(result_ids))
    ).all()

    by_result: dict[uuid.UUID, set[tuple[int, JudgeKind]]] = defaultdict(set)

    for judge in judges:
        if judge.status == JudgeStatus.SUCCEEDED and judge.criteria_score is not None:
            by_result[judge.grading_result_id].add((judge.judge_run_no, judge.kind))

    required = {
        (number, kind)
        for number in range(1, session.repetitions + 1)
        for kind in (JudgeKind.QUALITY, JudgeKind.FORMAT)
    }

    for result in results:
        if result.is_technical_disruption:
            continue

        if by_result[result.id] != required:
            raise ValueError("incomplete grading run: missing successful quality/format judges")


def publish_latest_run(
    db: Session,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Publish a complete run without deleting historical results."""

    session = db.scalar(
        select(MeasurementSession).where(MeasurementSession.id == session_id).with_for_update()
    )

    if session is None:
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
        rubric = db.get(
            RubricVersion,
            target.rubric_version_id,
        )

        if rubric is None or not rubric.is_active:
            raise ValueError("regrade must use the active rubric")

    assert_run_complete(db, session_id, run_id)

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

    db.flush()

    target.is_latest = True
    db.flush()
