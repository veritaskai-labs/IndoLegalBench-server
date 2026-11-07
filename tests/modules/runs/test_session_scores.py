"""SCRUM-225: latest attempt grades through case and product aggregation."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.runs.score_aggregation import GradePath, GradingResult
from app.modules.runs.session_scores import AttemptCase, aggregate_session_scores
from app.modules.runs.snapshot_cases import CaseFacts

NOW = datetime(2026, 11, 6, 15, tzinfo=UTC)


def grade(attempt, path, value, version=None, hour=15):
    return GradingResult(
        attempt_id=attempt,
        path=path,
        score=Decimal(value) if value is not None else None,
        rubric_version=version,
        graded_at=NOW.replace(hour=hour),
    )


def test_attempts_are_averaged_per_case_before_product():
    case_a, case_b, case_c = [uuid.uuid4() for _ in range(3)]
    a1, a2, b1, c1 = [uuid.uuid4() for _ in range(4)]

    facts = [
        CaseFacts(case_a, "A", SplitTag.DEV, "Pajak"),
        CaseFacts(case_b, "B", SplitTag.DEV, None),
        CaseFacts(case_c, "C", SplitTag.TEST, "Pajak"),
    ]

    attempts = [
        AttemptCase(a1, case_a),
        AttemptCase(a2, case_a),
        AttemptCase(b1, case_b),
        AttemptCase(c1, case_c),
    ]

    grades = [
        grade(a1, GradePath.DETERMINISTIC, "1.00"),
        grade(a2, GradePath.DETERMINISTIC, "0.50"),
        grade(b1, GradePath.DETERMINISTIC, "1.00"),
        grade(c1, GradePath.DETERMINISTIC, "0.25"),
        grade(a1, GradePath.JUDGE, "0.50", "v1", hour=10),
        grade(a1, GradePath.JUDGE, "1.00", "v2", hour=15),
        grade(a2, GradePath.JUDGE, "0.50", "v1"),
        grade(b1, GradePath.JUDGE, "1.00", "v1"),
        grade(c1, GradePath.JUDGE, "0.25", "v1"),
    ]

    result = aggregate_session_scores(facts, attempts, grades)

    assert result.totals.dev.deterministic == Decimal("0.88")
    assert result.totals.test.deterministic == Decimal("0.25")
    assert result.totals.combined.deterministic == Decimal("0.67")

    assert result.totals.dev.judge == Decimal("0.88")
    assert result.totals.combined.judge == Decimal("0.67")

    assert result.categories[0].category == "Pajak"
    assert result.categories[-1].category == "Lain-lain"
    assert [item.case_count for item in result.categories] == [2, 1]


def test_grade_from_another_session_is_rejected():
    fact = CaseFacts(uuid.uuid4(), "A", SplitTag.DEV, None)

    with pytest.raises(ValueError, match="outside this session"):
        aggregate_session_scores(
            [fact],
            [],
            [grade(uuid.uuid4(), GradePath.JUDGE, "0.50", "v1")],
        )


def test_missing_scores_are_none_not_zero():
    fact = CaseFacts(uuid.uuid4(), "A", SplitTag.DEV, None)

    result = aggregate_session_scores(
        [fact],
        [AttemptCase(uuid.uuid4(), fact.snapshot_item_id)],
        [],
    )

    assert result.totals.combined.judge is None
    assert result.totals.combined.deterministic is None


def test_no_repeated_rounding_between_repetitions_and_product():
    case_a, case_b = uuid.uuid4(), uuid.uuid4()
    a1, a2, b1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facts = [
        CaseFacts(case_a, "A", SplitTag.DEV, None),
        CaseFacts(case_b, "B", SplitTag.DEV, None),
    ]
    attempts = [AttemptCase(a1, case_a), AttemptCase(a2, case_a), AttemptCase(b1, case_b)]
    grades = [
        grade(a1, GradePath.DETERMINISTIC, "0.66"),
        grade(a2, GradePath.DETERMINISTIC, "0.67"),
        grade(b1, GradePath.DETERMINISTIC, "0.00"),
    ]
    from app.modules.runs.session_scores import aggregate_attempt_maps

    historical = aggregate_session_scores(facts, attempts, grades)
    mapped = aggregate_attempt_maps(
        facts, attempts, {a1: Decimal("0.66"), a2: Decimal("0.67"), b1: Decimal("0")}, {}
    )
    assert historical == mapped
    assert historical.cases[0].scores.deterministic == Decimal("0.67")
    assert historical.totals.combined.deterministic == Decimal("0.33")
