"""One shared scorer for historical grade rows and SQL per-attempt grade maps."""

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
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
    raw_mean_score,
    round_score,
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


def aggregate_attempt_maps(
    facts: Sequence[CaseFacts],
    attempts: Sequence[AttemptCase],
    deterministic: Mapping[uuid.UUID, Decimal | None],
    judge: Mapping[uuid.UUID, Decimal | None],
) -> SessionScores:
    """Validate membership once and roll up both paths without intermediate rounding."""
    case_ids = {fact.snapshot_item_id for fact in facts}
    attempt_to_case: dict[uuid.UUID, uuid.UUID] = {}

    for attempt in attempts:
        if attempt.snapshot_item_id not in case_ids:
            raise ValueError("attempt refers to a snapshot item outside this session")
        if attempt.attempt_id in attempt_to_case:
            raise ValueError("duplicate attempt id")
        attempt_to_case[attempt.attempt_id] = attempt.snapshot_item_id

    if (set(deterministic) | set(judge)) - attempt_to_case.keys():
        raise ValueError("grade refers to an attempt outside this session")

    grouped_det: dict[uuid.UUID, list[Decimal | None]] = defaultdict(list)
    grouped_judge: dict[uuid.UUID, list[Decimal | None]] = defaultdict(list)
    for attempt_id, case_id in attempt_to_case.items():
        grouped_det[case_id].append(deterministic.get(attempt_id))
        grouped_judge[case_id].append(judge.get(attempt_id))

    # Build two-decimal presentation values only AFTER the full-precision
    # totals and category averages have been calculated.
    raw_scores = {
        case_id: ScorePair(
            deterministic=raw_mean_score(grouped_det[case_id]),
            judge=raw_mean_score(grouped_judge[case_id]),
        )
        for case_id in case_ids
    }
    raw_cases = build_case_scores(facts, raw_scores)
    totals = three_scores(raw_cases)
    categories = category_breakdown(raw_cases)
    displayed_cases = tuple(
        CaseScore(
            case_code=case.case_code,
            split_tag=case.split_tag,
            category=case.category,
            scores=ScorePair(
                deterministic=round_score(case.scores.deterministic),
                judge=round_score(case.scores.judge),
            ),
        )
        for case in raw_cases
    )
    return SessionScores(cases=displayed_cases, totals=totals, categories=categories)


def aggregate_session_scores(
    facts: Sequence[CaseFacts],
    attempts: Sequence[AttemptCase],
    results: Sequence[GradingResult],
) -> SessionScores:
    """Adapt immutable grading history to the shared aggregation input format."""
    deterministic: dict[uuid.UUID, Decimal | None] = {}
    judge: dict[uuid.UUID, Decimal | None] = {}
    for grade in latest_results(results):
        if grade.path is GradePath.DETERMINISTIC:
            deterministic[grade.attempt_id] = grade.score
        else:
            judge[grade.attempt_id] = grade.score
    return aggregate_attempt_maps(facts, attempts, deterministic, judge)
