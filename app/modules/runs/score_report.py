"""Read-time session aggregation with an injected grading reader."""

import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.adapters import SnapshotCaseAdapter
from app.modules.runs.score_aggregation import (
    ScorePair,
    category_breakdown,
    repetition_score,
    three_scores,
)
from app.modules.runs.session_scores import SessionScores
from app.modules.runs.snapshot_cases import CaseFacts, build_case_scores


@dataclass(frozen=True)
class GradeScoreSnapshot:
    run_id: uuid.UUID | None
    rubric_version_id: uuid.UUID | None
    deterministic: Mapping[uuid.UUID, Decimal | None]
    quality: Mapping[uuid.UUID, Decimal | None]
    format: Mapping[uuid.UUID, Decimal | None]


class GradingReaderPort(Protocol):
    """A contract for SCRUM-222 data; no ORM dependency."""

    def latest(self, session_id: uuid.UUID) -> GradeScoreSnapshot: ...


@dataclass(frozen=True)
class SessionScoreReport:
    run_id: uuid.UUID | None
    rubric_version_id: uuid.UUID | None
    quality: SessionScores
    format: SessionScores


def _aggregate(
    facts: tuple[CaseFacts, ...],
    attempts: list[tuple[uuid.UUID, uuid.UUID]],
    deterministic: Mapping[uuid.UUID, Decimal | None],
    judge: Mapping[uuid.UUID, Decimal | None],
) -> SessionScores:
    valid_cases = {fact.snapshot_item_id for fact in facts}
    valid_attempts: set[uuid.UUID] = set()

    grouped_det: dict[uuid.UUID, list[Decimal | None]] = defaultdict(list)
    grouped_judge: dict[uuid.UUID, list[Decimal | None]] = defaultdict(list)

    for attempt_id, case_id in attempts:
        if case_id not in valid_cases:
            raise ValueError("attempt refers to a case outside this session")

        if attempt_id in valid_attempts:
            raise ValueError("duplicate attempt id")

        valid_attempts.add(attempt_id)

        grouped_det[case_id].append(deterministic.get(attempt_id))
        grouped_judge[case_id].append(judge.get(attempt_id))

    if (set(deterministic) | set(judge)) - valid_attempts:
        raise ValueError("grade refers to an attempt outside this session")

    case_scores = {
        case_id: ScorePair(
            deterministic=repetition_score(grouped_det[case_id]),
            judge=repetition_score(grouped_judge[case_id]),
        )
        for case_id in valid_cases
    }

    cases = build_case_scores(facts, case_scores)

    return SessionScores(
        cases=cases,
        totals=three_scores(cases),
        categories=category_breakdown(cases),
    )


def read_session_report(
    db: Session,
    session_id: uuid.UUID,
    reader: GradingReaderPort,
) -> SessionScoreReport:
    """Calculate quality and format independently, without persisting them."""
    facts = SnapshotCaseAdapter(db).case_facts(session_id)
    attempts = repository.session_attempt_items(db, session_id)
    stored = reader.latest(session_id)

    return SessionScoreReport(
        run_id=stored.run_id,
        rubric_version_id=stored.rubric_version_id,
        quality=_aggregate(facts, attempts, stored.deterministic, stored.quality),
        format=_aggregate(facts, attempts, stored.deterministic, stored.format),
    )
