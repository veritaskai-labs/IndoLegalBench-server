
"""Regression tests for the updated SCRUM-225 scoring contract."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.modules.runs import score_aggregation as scores
from app.modules.runs.score_aggregation import GradePath, GradingResult, RubricVersion


def grade(attempt, path, score, version=None, hour=10):
    return GradingResult(
        attempt_id=attempt,
        path=path,
        score=Decimal(score),
        rubric_version=version,
        graded_at=datetime(2026, 11, 6, hour, tzinfo=UTC),
    )


def test_no_combined_weighted_attempt_score():
    assert not hasattr(scores, "attempt_score")


def test_latest_is_independent_for_deterministic_and_judge():
    attempt = uuid.uuid4()
    rows = [
        grade(attempt, GradePath.DETERMINISTIC, "0.40"),
        grade(attempt, GradePath.JUDGE, "0.50", "v1"),
        grade(attempt, GradePath.JUDGE, "0.90", "v2", hour=11),
    ]

    marked = scores.mark_latest(rows)

    assert [item.is_latest for item in marked] == [True, False, True]
    assert len(scores.latest_results(rows)) == 2


def test_only_judge_requires_rubric_upgrade():
    attempt = uuid.uuid4()
    rows = [
        grade(attempt, GradePath.DETERMINISTIC, "0.40"),
        grade(attempt, GradePath.JUDGE, "0.50", "v1"),
    ]

    assert scores.needs_regrade(rows, RubricVersion("v2")) == (attempt,)
    assert scores.needs_regrade(rows, RubricVersion("v1")) == ()
