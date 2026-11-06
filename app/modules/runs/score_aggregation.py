
"""Score aggregation for PBI-14 SCRUM-225."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from app.modules.cases.models import SplitTag

LAIN_LAIN = "Lain-lain"


class RubricAspect(StrEnum):
    KESIMPULAN = "kesimpulan"
    DASAR_HUKUM = "dasar_hukum"
    ARGUMENTASI = "argumentasi"


@dataclass(frozen=True)
class RubricVersion:
    version: str
    max_aspect_score: Decimal
    weights: Mapping[RubricAspect, Decimal]


RUBRIC_V1 = RubricVersion(
    version="v1",
    max_aspect_score=Decimal(5),
    weights=MappingProxyType(
        {
            RubricAspect.KESIMPULAN: Decimal(1),
            RubricAspect.DASAR_HUKUM: Decimal(1),
            RubricAspect.ARGUMENTASI: Decimal(1),
        }
    ),
)


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
    attempt_id: uuid.UUID
    rubric_version: str
    graded_at: datetime
    scores: ScorePair


@dataclass(frozen=True)
class GradingRecord:
    result: GradingResult
    is_latest: bool


def attempt_score(aspects, rubric=RUBRIC_V1):
    raise NotImplementedError


def criteria_score(met, total):
    raise NotImplementedError


def mean_score(scores):
    raise NotImplementedError


def repetition_score(attempt_scores):
    raise NotImplementedError


def pair_mean(cases):
    raise NotImplementedError


def product_score(cases):
    raise NotImplementedError


def three_scores(cases):
    raise NotImplementedError


def category_breakdown(cases):
    raise NotImplementedError


def mark_latest(results):
    raise NotImplementedError


def latest_results(results):
    raise NotImplementedError


def needs_regrade(results, rubric=RUBRIC_V1):
    raise NotImplementedError
