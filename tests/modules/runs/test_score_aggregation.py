"""SCRUM-225: independent scoring paths and read-time aggregation."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.runs import score_aggregation
from app.modules.runs.score_aggregation import (
    LAIN_LAIN,
    CaseScore,
    GradePath,
    GradingResult,
    RubricVersion,
    ScorePair,
    category_breakdown,
    latest_results,
    mark_latest,
    mean_score,
    needs_regrade,
    product_score,
    repetition_score,
    three_scores,
)


def case(code, split, deterministic, judge, category=None):
    return CaseScore(
        code,
        split,
        category,
        ScorePair(deterministic, judge),
    )


def row(attempt, path, score, version=None, hour=10):
    return GradingResult(
        attempt_id=attempt,
        path=path,
        score=score,
        rubric_version=version,
        graded_at=datetime(2026, 11, 6, hour, tzinfo=UTC),
    )


def test_no_weighted_attempt_score():
    assert not hasattr(score_aggregation, "attempt_score")


def test_attempt_paths_remain_separate():
    result = case("A", SplitTag.DEV, Decimal("0.25"), Decimal("0.75"))
    assert result.scores == ScorePair(Decimal("0.25"), Decimal("0.75"))


def test_repetition_ignores_none_but_keeps_zero():
    scores = [Decimal("0.00"), None, Decimal("1.00")]

    assert repetition_score(scores) == Decimal("0.50")
    assert repetition_score([None]) is None


def test_rounds_to_two_decimals():
    scores = [
        Decimal("0.00"),
        Decimal("1.00"),
        Decimal("1.00"),
    ]
    assert mean_score(scores) == Decimal("0.67")


def test_product_uses_equal_case_weights():
    cases = [
        case("A", SplitTag.DEV, Decimal("1.00"), Decimal("0.80")),
        case("B", SplitTag.DEV, Decimal("0.50"), Decimal("0.40")),
    ]

    assert product_score(cases) == ScorePair(Decimal("0.75"), Decimal("0.60"))


def test_combined_is_average_of_cases_not_split_averages():
    cases = [
        case("A", SplitTag.DEV, Decimal("1.00"), Decimal("1.00")),
        case("B", SplitTag.DEV, Decimal("1.00"), Decimal("1.00")),
        case("C", SplitTag.TEST, Decimal("0.00"), Decimal("0.00")),
    ]

    result = three_scores(cases)

    assert result.dev.judge == Decimal("1.00")
    assert result.test.judge == Decimal("0.00")
    assert result.combined.judge == Decimal("0.67")
    assert result.combined.deterministic == Decimal("0.67")


def test_absent_split_stays_none():
    cases = [case("A", SplitTag.DEV, Decimal("0.50"), None)]

    result = three_scores(cases)

    assert result.test == ScorePair(None, None)
    assert result.dev.judge is None


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_missing_category_goes_to_lain_lain(empty):
    cases = [case("A", SplitTag.DEV, None, Decimal("0.25"), empty)]

    result = category_breakdown(cases)

    assert result[0].category == LAIN_LAIN
    assert result[0].case_count == 1
    assert result[0].scores.judge == Decimal("0.25")


def test_category_order_has_lain_lain_last():
    cases = [
        case("A", SplitTag.DEV, None, None, None),
        case("B", SplitTag.DEV, None, None, "zakat"),
        case("C", SplitTag.TEST, None, None, "aset"),
    ]

    categories = category_breakdown(cases)

    assert [item.category for item in categories] == [
        "aset",
        "zakat",
        LAIN_LAIN,
    ]


def test_latest_is_independent_per_path():
    attempt = uuid.uuid4()

    history = [
        row(attempt, GradePath.DETERMINISTIC, Decimal("0.40")),
        row(attempt, GradePath.JUDGE, Decimal("0.50"), "v1"),
        row(attempt, GradePath.JUDGE, Decimal("0.90"), "v2", hour=11),
    ]

    assert [item.is_latest for item in mark_latest(history)] == [True, False, True]

    assert [(item.path, item.score) for item in latest_results(history)] == [
        (GradePath.DETERMINISTIC, Decimal("0.40")),
        (GradePath.JUDGE, Decimal("0.90")),
    ]


def test_equal_timestamps_use_stable_input_order():
    attempt = uuid.uuid4()
    history = [
        row(attempt, GradePath.JUDGE, Decimal("0.50"), "v1"),
        row(attempt, GradePath.JUDGE, Decimal("0.75"), "v2"),
    ]

    assert latest_results(history) == (history[1],)


def test_only_judge_needs_new_rubric():
    attempt = uuid.uuid4()
    history = [
        row(attempt, GradePath.DETERMINISTIC, Decimal("0.40")),
        row(attempt, GradePath.JUDGE, Decimal("0.50"), "v1"),
    ]

    assert needs_regrade(history, RubricVersion("v2")) == (attempt,)

    assert needs_regrade(history, RubricVersion("v1")) == ()
