"""SCRUM-225: aggregate scores without importing SCRUM-222 models."""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.modules.cases.models import SplitTag
from app.modules.runs.score_report import (
    GradeScoreSnapshot,
    read_session_report,
)
from app.modules.runs.snapshot_cases import CaseFacts


class FakeGradingReader:
    def __init__(self, session_id, scores):
        self.session_id = session_id
        self.scores = scores

    def latest(self, session_id):
        assert session_id == self.session_id
        return self.scores


def test_quality_and_format_are_aggregated_separately():
    session_id = uuid.uuid4()
    dev, test = uuid.uuid4(), uuid.uuid4()
    a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    facts = (
        CaseFacts(dev, "D1", SplitTag.DEV, None),
        CaseFacts(test, "T1", SplitTag.TEST, "pajak"),
    )

    scores = GradeScoreSnapshot(
        run_id=uuid.uuid4(),
        rubric_version_id=uuid.uuid4(),
        deterministic={
            a1: Decimal("1.00"),
            a2: Decimal("0.50"),
            a3: Decimal("0.40"),
        },
        quality={
            a1: Decimal("1.00"),
            a2: Decimal("0.50"),
            a3: Decimal("0.25"),
        },
        format={
            a1: Decimal("0.50"),
            a2: Decimal("0.50"),
            a3: Decimal("1.00"),
        },
    )

    with (
        patch(
            "app.modules.runs.score_report.SnapshotCaseAdapter.case_facts",
            return_value=facts,
        ),
        patch(
            "app.modules.runs.score_report.repository.session_attempt_items",
            return_value=[(a1, dev), (a2, dev), (a3, test)],
        ),
    ):
        report = read_session_report(
            object(),
            session_id,
            FakeGradingReader(session_id, scores),
        )

    assert report.quality.totals.dev.deterministic == Decimal("0.75")
    assert report.quality.totals.dev.judge == Decimal("0.75")
    assert report.quality.totals.test.judge == Decimal("0.25")

    assert report.format.totals.dev.judge == Decimal("0.50")
    assert report.format.totals.test.judge == Decimal("1.00")

    assert report.quality.totals.combined.judge == Decimal("0.50")
    assert report.format.totals.combined.judge == Decimal("0.75")

    assert report.quality.categories[0].category == "pajak"
    assert report.quality.categories[-1].category == "Lain-lain"


def test_missing_grades_do_not_become_zero():
    session_id = uuid.uuid4()
    case_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    scores = GradeScoreSnapshot(
        run_id=None,
        rubric_version_id=None,
        deterministic={},
        quality={},
        format={},
    )

    with (
        patch(
            "app.modules.runs.score_report.SnapshotCaseAdapter.case_facts",
            return_value=(CaseFacts(case_id, "A", SplitTag.DEV, None),),
        ),
        patch(
            "app.modules.runs.score_report.repository.session_attempt_items",
            return_value=[(attempt_id, case_id)],
        ),
    ):
        report = read_session_report(
            object(),
            session_id,
            FakeGradingReader(session_id, scores),
        )

    assert report.quality.totals.combined.judge is None
    assert report.format.totals.combined.judge is None
    assert report.quality.totals.combined.deterministic is None


def test_foreign_grades_are_rejected():
    session_id = uuid.uuid4()
    foreign_attempt = uuid.uuid4()

    scores = GradeScoreSnapshot(
        run_id=uuid.uuid4(),
        rubric_version_id=uuid.uuid4(),
        deterministic={foreign_attempt: Decimal("0.50")},
        quality={},
        format={},
    )

    with (
        patch(
            "app.modules.runs.score_report.SnapshotCaseAdapter.case_facts",
            return_value=(),
        ),
        patch(
            "app.modules.runs.score_report.repository.session_attempt_items",
            return_value=[],
        ),
        pytest.raises(ValueError, match="outside this session"),
    ):
        read_session_report(
            object(),
            session_id,
            FakeGradingReader(session_id, scores),
        )
