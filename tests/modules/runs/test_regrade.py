"""SCRUM-225: append-only regrading of judge scores."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.runs.regrade import RegradeService
from app.modules.runs.score_aggregation import (
    GradePath,
    GradingResult,
    RubricVersion,
)

NOW = datetime(2026, 11, 6, 15, tzinfo=UTC)
SESSION = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.writes = []

    def results(self, session_id):
        return tuple(self.rows)

    def append(self, session_id, rows):
        self.writes.append(rows)
        self.rows.extend(rows)


class FakeGrader:
    def __init__(self, score=Decimal("0.90")):
        self.score = score
        self.calls = []

    def grade(self, attempt_id, rubric):
        self.calls.append((attempt_id, rubric.version))
        return self.score


class FakeRubrics:
    def __init__(self, version):
        self.version = version

    def latest(self):
        return RubricVersion(self.version)


def grade(
    attempt_id,
    path=GradePath.JUDGE,
    version="v1",
    score="0.50",
):
    return GradingResult(
        attempt_id=attempt_id,
        path=path,
        score=Decimal(score),
        graded_at=NOW.replace(hour=10),
        rubric_version=(version if path is GradePath.JUDGE else None),
    )


def service(store, grader=None, version="v2"):
    return RegradeService(
        store=store,
        grader=grader or FakeGrader(),
        rubrics=FakeRubrics(version),
        clock=lambda: NOW,
    )


def test_regrade_only_judge_and_keep_deterministic():
    attempt = uuid.uuid4()
    deterministic = grade(attempt, GradePath.DETERMINISTIC, score="0.40")
    old_judge = grade(attempt)

    store = FakeStore([deterministic, old_judge])
    grader = FakeGrader()

    result = service(store, grader).regrade(SESSION)

    assert len(store.rows) == 3
    assert [item.path for item in store.rows] == [
        GradePath.DETERMINISTIC,
        GradePath.JUDGE,
        GradePath.JUDGE,
    ]
    assert [item.is_latest for item in result.history] == [True, False, True]
    assert grader.calls == [(attempt, "v2")]
    assert store.rows[0] is deterministic


def test_second_regrade_does_not_append_again():
    attempt = uuid.uuid4()
    store = FakeStore([grade(attempt)])
    runner = service(store)

    runner.regrade(SESSION)
    result = runner.regrade(SESSION)

    assert result.regraded == ()
    assert len(store.writes) == 1


def test_regrade_uses_latest_rubric():
    attempt = uuid.uuid4()
    store = FakeStore([grade(attempt, version="v2")])
    rubrics = FakeRubrics("v3")
    grader = FakeGrader()

    runner = RegradeService(
        store=store,
        grader=grader,
        rubrics=rubrics,
        clock=lambda: NOW,
    )

    runner.regrade(SESSION)

    assert store.rows[-1].rubric_version == "v3"


def test_failed_judge_score_does_not_replace_old_result():
    attempt = uuid.uuid4()
    old = grade(attempt)
    store = FakeStore([old])
    grader = FakeGrader(score=None)

    with pytest.raises(ValueError, match="no judge score"):
        service(store, grader).regrade(SESSION)

    assert store.rows == [old]
    assert store.writes == []


def test_grader_exception_does_not_append_partial_batch():
    first, second = uuid.uuid4(), uuid.uuid4()
    store = FakeStore([grade(first), grade(second)])

    class FailingGrader(FakeGrader):
        def grade(self, attempt_id, rubric):
            if attempt_id == second:
                raise RuntimeError("network")
            return Decimal("0.80")

    with pytest.raises(RuntimeError, match="network"):
        service(store, FailingGrader()).regrade(SESSION)

    assert len(store.rows) == 2
    assert store.writes == []


def test_deterministic_only_result_is_not_regraded():
    attempt = uuid.uuid4()
    store = FakeStore([grade(attempt, GradePath.DETERMINISTIC)])
    grader = FakeGrader()

    result = service(store, grader).regrade(SESSION)

    assert result.regraded == ()
    assert grader.calls == []
