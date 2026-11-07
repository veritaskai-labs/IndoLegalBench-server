"""SQL-backed regrading with append-only grading history."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.grading.models import (
    GradingResult,
    GradingResultReason,
    GradingRun,
    GradingRunKind,
    GradingRunStatus,
    JudgeKind,
    JudgeStatus,
    LlmCriterionResult,
    LlmJudgeResult,
    RubricVersion,
)
from app.modules.grading.run_service import (
    assert_run_complete,
    publish_latest_run,
)
from app.modules.runs.models import (
    Attempt,
    MeasurementSession,
)


@dataclass(frozen=True)
class JudgeCriterion:
    key: str
    text: str
    met: bool
    explanation: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class JudgeOutcome:
    attempt_id: uuid.UUID
    kind: JudgeKind
    judge_run_no: int
    prompt: str
    criteria: tuple[JudgeCriterion, ...]
    explanation: str | None = None
    rubric_level: int | None = None
    model_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_idr: Decimal | None = None
    usd_idr_rate: Decimal | None = None
    raw_response: dict | None = None


def start_sql_regrade(
    db: Session,
    session_id: uuid.UUID,
    triggered_by: uuid.UUID,
) -> uuid.UUID | None:
    """Start a new grading run using the active rubric."""

    session = db.scalar(
        select(MeasurementSession).where(MeasurementSession.id == session_id).with_for_update()
    )

    if session is None:
        raise ValueError("session not found")

    rubric = db.scalar(select(RubricVersion).where(RubricVersion.is_active.is_(True)))

    if rubric is None or rubric.locked_at is None:
        raise ValueError("no active and locked rubric version")

    pending = db.scalar(
        select(GradingRun.id).where(
            GradingRun.session_id == session_id,
            GradingRun.status.in_(
                (
                    GradingRunStatus.QUEUED,
                    GradingRunStatus.RUNNING,
                )
            ),
        )
    )

    if pending is not None:
        raise ValueError("grading run already in progress")

    latest = db.scalar(
        select(GradingRun)
        .where(
            GradingRun.session_id == session_id,
            GradingRun.is_latest.is_(True),
        )
        .with_for_update()
    )

    if latest is None or latest.status != GradingRunStatus.FINISHED:
        raise ValueError("finished initial grading required before regrade")

    if latest.rubric_version_id == rubric.id:
        return None

    old_results = db.scalars(
        select(GradingResult).where(GradingResult.grading_run_id == latest.id)
    ).all()

    expected = set(db.scalars(select(Attempt.id).where(Attempt.session_id == session_id)))

    if (
        not expected
        or len(old_results) != len(expected)
        or {row.attempt_id for row in old_results} != expected
    ):
        raise ValueError("old grading run is incomplete")

    run = GradingRun(
        session_id=session_id,
        rubric_version_id=rubric.id,
        kind=GradingRunKind.REGRADE,
        status=GradingRunStatus.QUEUED,
        is_latest=False,
        triggered_by=triggered_by,
    )

    db.add(run)
    db.flush()

    # PostgreSQL requires QUEUED -> RUNNING.
    run.status = GradingRunStatus.RUNNING
    db.flush()

    for old in old_results:
        fresh = GradingResult(
            grading_run_id=run.id,
            attempt_id=old.attempt_id,
            is_technical_disruption=old.is_technical_disruption,
            det_score=old.det_score,
            det_regulation_matches=old.det_regulation_matches,
            det_article_matches=old.det_article_matches,
            det_ungradable_refs=old.det_ungradable_refs,
            det_all_chained_cited=old.det_all_chained_cited,
            det_reference_total=old.det_reference_total,
            det_reference_correct=old.det_reference_correct,
            det_extracted_total=old.det_extracted_total,
            det_extracted_correct=old.det_extracted_correct,
            det_formula=old.det_formula,
        )

        db.add(fresh)
        db.flush()

        old_reasons = db.scalars(
            select(GradingResultReason).where(GradingResultReason.grading_result_id == old.id)
        ).all()

        for reason in old_reasons:
            db.add(
                GradingResultReason(
                    grading_result_id=fresh.id,
                    reason_code=reason.reason_code,
                )
            )

    db.flush()
    return run.id


def _running_result(
    db: Session,
    run_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> GradingResult:
    """Return an attempt result belonging to a running run."""

    run = db.get(GradingRun, run_id)

    if run is None or run.status != GradingRunStatus.RUNNING:
        raise ValueError("grading run is not running")

    result = db.scalar(
        select(GradingResult).where(
            GradingResult.grading_run_id == run_id,
            GradingResult.attempt_id == attempt_id,
        )
    )

    if result is None or result.is_technical_disruption:
        raise ValueError("attempt is not judgeable in this run")

    return result


def append_sql_judgement(
    db: Session,
    run_id: uuid.UUID,
    output: JudgeOutcome,
) -> None:
    """Append a successful judge result and its criteria."""

    result = _running_result(db, run_id, output.attempt_id)

    if not output.criteria or not output.prompt.strip():
        raise ValueError("judge output requires prompt and criteria")

    if not 1 <= output.judge_run_no <= 5:
        raise ValueError("judge_run_no must be 1..5")

    if len({c.key for c in output.criteria}) != len(output.criteria):
        raise ValueError("duplicate judge criterion key")

    met = sum(1 for criterion in output.criteria if criterion.met)

    total = len(output.criteria)

    score = (Decimal(met) / Decimal(total)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    judge = LlmJudgeResult(
        grading_result_id=result.id,
        kind=output.kind,
        judge_run_no=output.judge_run_no,
        status=JudgeStatus.SUCCEEDED,
        prompt=output.prompt,
        criteria_met=met,
        criteria_total=total,
        criteria_score=score,
        rubric_level=output.rubric_level,
        explanation=output.explanation,
        model_version=output.model_version,
        input_tokens=output.input_tokens,
        output_tokens=output.output_tokens,
        cost_idr=output.cost_idr,
        usd_idr_rate=output.usd_idr_rate,
        raw_response=output.raw_response,
    )

    db.add(judge)
    db.flush()

    for criterion in output.criteria:
        reason_code = None

        if not criterion.met:
            reason_code = criterion.reason_code or "criterion_unmet"

        db.add(
            LlmCriterionResult(
                llm_judge_result_id=judge.id,
                criterion_key=criterion.key,
                criterion_text=criterion.text,
                is_met=criterion.met,
                explanation=criterion.explanation,
                reason_code=reason_code,
            )
        )

    db.flush()


def append_sql_judge_failure(
    db: Session,
    run_id: uuid.UUID,
    attempt_id: uuid.UUID,
    kind: JudgeKind,
    judge_run_no: int,
    prompt: str,
    failure_kind: str = "provider_error",
) -> None:
    """Record a failed judge call without a valid score."""

    result = _running_result(db, run_id, attempt_id)

    if not prompt.strip():
        raise ValueError("judge prompt is empty")

    db.add(
        LlmJudgeResult(
            grading_result_id=result.id,
            kind=kind,
            judge_run_no=judge_run_no,
            status=JudgeStatus.FAILED,
            prompt=prompt,
            failure_kind=failure_kind,
            error="judge execution failed",
        )
    )

    db.flush()


def finish_sql_regrade(
    db: Session,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Finish and publish only when grading is complete."""

    session = db.scalar(
        select(MeasurementSession).where(MeasurementSession.id == session_id).with_for_update()
    )

    if session is None:
        raise ValueError("session not found")

    run = db.scalar(
        select(GradingRun)
        .where(
            GradingRun.id == run_id,
            GradingRun.session_id == session_id,
        )
        .with_for_update()
    )

    if run is None or run.kind != GradingRunKind.REGRADE:
        raise ValueError("not a regrade run belonging to this session")

    if run.status != GradingRunStatus.RUNNING:
        raise ValueError("regrade is not running")

    rubric = db.get(
        RubricVersion,
        run.rubric_version_id,
    )

    if rubric is None or not rubric.is_active:
        raise ValueError("regrade rubric is no longer active")

    assert_run_complete(db, session_id, run_id)

    run.status = GradingRunStatus.FINISHED
    run.finished_at = datetime.now(UTC)

    db.flush()

    publish_latest_run(db, session_id, run_id)


def mark_sql_regrade_failed(
    db: Session,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    """Fail a run while preserving the previous latest run."""

    run = db.scalar(
        select(GradingRun)
        .where(
            GradingRun.id == run_id,
            GradingRun.session_id == session_id,
        )
        .with_for_update()
    )

    if run is None or run.kind != GradingRunKind.REGRADE:
        raise ValueError("not a regrade run belonging to this session")

    if run.is_latest or run.status == GradingRunStatus.FINISHED:
        raise ValueError("cannot fail an already completed run")

    if run.status == GradingRunStatus.FAILED:
        return

    run.status = GradingRunStatus.FAILED
    run.finished_at = datetime.now(UTC)

    db.flush()
