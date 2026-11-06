"""Separate deterministic and judge scores without storing aggregates."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.modules.cases.models import SplitTag

_CENT = Decimal("0.01")
LAIN_LAIN = "Lain-lain"


class GradePath(StrEnum):
    DETERMINISTIC = "deterministic"
    JUDGE = "judge"


@dataclass(frozen=True)
class RubricVersion:
    version: str


@dataclass(frozen=True)
class ScorePair:
    deterministic: Decimal | None
    judge: Decimal | None


@dataclass(frozen=True)
class CaseScore:
    case_code: str
    split_tag: SplitTag
    category: str | None
    scores: ScorePair


@dataclass(frozen=True)
class ThreeScores:
    dev: ScorePair
    test: ScorePair
    combined: ScorePair


@dataclass(frozen=True)
class CategoryScore:
    category: str
    case_count: int
    scores: ScorePair


@dataclass(frozen=True)
class GradingResult:
    """One historical score for one attempt and one grading path."""

    attempt_id: uuid.UUID
    path: GradePath
    score: Decimal | None
    graded_at: datetime
    rubric_version: str | None = None


@dataclass(frozen=True)
class GradingRecord:
    result: GradingResult
    is_latest: bool


def mean_score(scores: Iterable[Decimal | None]) -> Decimal | None:
    values = [score for score in scores if score is not None]
    if not values:
        return None

    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(_CENT, rounding=ROUND_HALF_UP)


def repetition_score(attempt_scores: Iterable[Decimal | None]) -> Decimal | None:
    """Case score from the mean of its graded repetitions."""
    return mean_score(attempt_scores)


def pair_mean(cases: Sequence[CaseScore]) -> ScorePair:
    """Average both paths independently, never combining them."""
    return ScorePair(
        deterministic=mean_score(case.scores.deterministic for case in cases),
        judge=mean_score(case.scores.judge for case in cases),
    )


def product_score(cases: Sequence[CaseScore]) -> ScorePair:
    """Every case has equal weight in the product average."""
    return pair_mean(cases)


def three_scores(cases: Sequence[CaseScore]) -> ThreeScores:
    """Calculate dev, test and combined without persisting the results."""
    dev = [case for case in cases if case.split_tag is SplitTag.DEV]
    test = [case for case in cases if case.split_tag is SplitTag.TEST]

    return ThreeScores(
        dev=pair_mean(dev),
        test=pair_mean(test),
        combined=pair_mean(cases),
    )


def category_breakdown(cases: Sequence[CaseScore]) -> tuple[CategoryScore, ...]:
    """Use snapshot categories; unclassified cases belong to Lain-lain."""
    groups: dict[str, list[CaseScore]] = {}

    for case in cases:
        name = (case.category or "").strip() or LAIN_LAIN
        groups.setdefault(name, []).append(case)

    names = sorted(name for name in groups if name != LAIN_LAIN)

    if LAIN_LAIN in groups:
        names.append(LAIN_LAIN)

    return tuple(
        CategoryScore(
            category=name,
            case_count=len(groups[name]),
            scores=pair_mean(groups[name]),
        )
        for name in names
    )


def mark_latest(results: Sequence[GradingResult]) -> tuple[GradingRecord, ...]:
    """Mark the latest result independently for each attempt and grading path.

    When timestamps tie, the later input wins. A database adapter must
    supply a deterministic order for results with identical timestamps.
    """
    latest: dict[tuple[uuid.UUID, GradePath], int] = {}

    for index, result in enumerate(results):
        key = (result.attempt_id, result.path)
        previous = latest.get(key)

        if previous is None or result.graded_at >= results[previous].graded_at:
            latest[key] = index

    positions = set(latest.values())

    return tuple(
        GradingRecord(
            result=result,
            is_latest=index in positions,
        )
        for index, result in enumerate(results)
    )


def latest_results(results: Sequence[GradingResult]) -> tuple[GradingResult, ...]:
    """Read the latest score for each attempt and path."""
    return tuple(record.result for record in mark_latest(results) if record.is_latest)


def needs_regrade(
    results: Sequence[GradingResult],
    rubric: RubricVersion,
) -> tuple[uuid.UUID, ...]:
    """Only judge scores depend on the active rubric version."""
    return tuple(
        result.attempt_id
        for result in latest_results(results)
        if result.path is GradePath.JUDGE and result.rubric_version != rubric.version
    )
