"""Read-time aggregation of scores from attempts and frozen case snapshots."""

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.modules.runs.score_aggregation import (
    CaseScore,
    CategoryScore,
    GradePath,
    GradingResult,
    ScorePair,
    ThreeScores,
    category_breakdown,
    latest_results,
    repetition_score,
    three_scores,
)
from app.modules.runs.snapshot_cases import CaseFacts, build_case_scores


@dataclass(frozen=True)
class AttemptCase:
    attempt_id: uuid.UUID
    snapshot_item_id: uuid.UUID


@dataclass(frozen=True)
class SessionScores:
    cases: tuple[CaseScore, ...]
    totals: ThreeScores
    categories: tuple[CategoryScore, ...]


def aggregate_session_scores(
    facts: Sequence[CaseFacts],
    attempts: Sequence[AttemptCase],
    results: Sequence[GradingResult],
) -> SessionScores:
    """Calculate the scores on demand without persisting aggregate columns."""
    case_ids = {fact.snapshot_item_id for fact in facts}
    attempt_to_case: dict[uuid.UUID, uuid.UUID] = {}

    for attempt in attempts:
        if attempt.snapshot_item_id not in case_ids:
            raise ValueError("attempt refers to a snapshot item outside this session")

        if attempt.attempt_id in attempt_to_case:
            raise ValueError("duplicate attempt id")

        attempt_to_case[attempt.attempt_id] = attempt.snapshot_item_id

    grouped: dict[tuple[uuid.UUID, GradePath], list[Decimal]] = defaultdict(list)

    for grade in latest_results(results):
        if grade.attempt_id not in attempt_to_case:
            raise ValueError("grade refers to an attempt outside this session")

        if grade.score is not None:
            case_id = attempt_to_case[grade.attempt_id]
            grouped[(case_id, grade.path)].append(grade.score)

    scores = {
        case_id: ScorePair(
            deterministic=repetition_score(grouped[(case_id, GradePath.DETERMINISTIC)]),
            judge=repetition_score(grouped[(case_id, GradePath.JUDGE)]),
        )
        for case_id in case_ids
    }

    cases = build_case_scores(facts, scores)

    return SessionScores(
        cases=cases,
        totals=three_scores(cases),
        categories=category_breakdown(cases),
    )
