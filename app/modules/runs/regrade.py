"""Append-only judge regrading with the latest active rubric."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.modules.runs.score_aggregation import (
    GradePath,
    GradingRecord,
    GradingResult,
    RubricVersion,
    mark_latest,
    needs_regrade,
)


class GradingStorePort(Protocol):
    def results(self, session_id: uuid.UUID) -> tuple[GradingResult, ...]: ...

    def append(
        self,
        session_id: uuid.UUID,
        results: tuple[GradingResult, ...],
    ) -> None: ...


class AttemptGraderPort(Protocol):
    def grade(
        self,
        attempt_id: uuid.UUID,
        rubric: RubricVersion,
    ) -> Decimal | None: ...


class ActiveRubricPort(Protocol):
    def latest(self) -> RubricVersion: ...


@dataclass(frozen=True)
class RegradeOutcome:
    regraded: tuple[uuid.UUID, ...]
    skipped: tuple[uuid.UUID, ...]
    history: tuple[GradingRecord, ...]


class RegradeService:
    def __init__(
        self,
        *,
        store: GradingStorePort,
        grader: AttemptGraderPort,
        rubrics: ActiveRubricPort,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._grader = grader
        self._rubrics = rubrics
        self._clock = clock

    def regrade(self, session_id: uuid.UUID) -> RegradeOutcome:
        rubric = self._rubrics.latest()
        previous = self._store.results(session_id)
        outdated = needs_regrade(previous, rubric)

        judge_attempts = tuple(
            dict.fromkeys(
                row.result.attempt_id
                for row in mark_latest(previous)
                if row.is_latest and row.result.path is GradePath.JUDGE
            )
        )

        if not outdated:
            return RegradeOutcome(
                regraded=(),
                skipped=judge_attempts,
                history=mark_latest(previous),
            )

        graded_at = self._clock()
        new_rows: list[GradingResult] = []

        for attempt_id in outdated:
            score = self._grader.grade(attempt_id, rubric)

            if score is None:
                raise ValueError("regrade returned no judge score")

            new_rows.append(
                GradingResult(
                    attempt_id=attempt_id,
                    path=GradePath.JUDGE,
                    score=score,
                    rubric_version=rubric.version,
                    graded_at=graded_at,
                )
            )

        self._store.append(session_id, tuple(new_rows))

        return RegradeOutcome(
            regraded=outdated,
            skipped=tuple(
                attempt_id for attempt_id in judge_attempts if attempt_id not in outdated
            ),
            history=mark_latest((*previous, *new_rows)),
        )
