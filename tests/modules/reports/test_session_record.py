"""Data sesi untuk pembuat laporan. PBI-15 SCRUM-79 SCRUM-313.

Murni, tanpa database.
"""

import uuid
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import CaseRef, SessionFacts
from app.modules.reports.models import MeasurementKind, ScoreScope, ScoreSplit
from app.modules.reports.report_scoring import CallCounts, GradedCase
from app.modules.reports.session_record import (
    SessionRecord,
    calls_for_split,
    facts_for_scope,
)
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType


def _kasus(nomor: int, split: SplitTag) -> GradedCase:
    return GradedCase(
        ref=CaseRef(
            case_id=uuid.UUID(f"{nomor:032x}"),
            case_version_id=uuid.UUID(f"{nomor:016x}{1:016x}"),
            case_code=f"CASE-{nomor:03d}",
        ),
        split=split,
        det=Decimal("0.5"),
        llm=Decimal("0.5"),
    )


def _rekaman(**ubah) -> SessionRecord:
    graded = ubah.pop(
        "graded_cases",
        (_kasus(1, SplitTag.TEST), _kasus(2, SplitTag.DEV), _kasus(3, SplitTag.TEST)),
    )
    facts = SessionFacts(
        session_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        product_name="Produk",
        session_type=ubah.pop("session_type", SessionType.AUTOMATIC),
        purpose=SessionPurpose.OFFICIAL,
        status=SessionStatus.FINISHED,
        accepted_as_is_at=None,
        rubric_version_id=uuid.uuid4(),
        judge_provider_id=uuid.uuid4(),
        judge_model_version="judge-1",
        suite_ids=frozenset({uuid.uuid4()}),
        model_version="model-1",
        repetitions=1,
        answer_date_from=None,
        answer_date_to=None,
        scored_cases=tuple(kasus.ref for kasus in graded),
        snapshot_cases=tuple(kasus.ref for kasus in graded),
    )
    return SessionRecord(
        facts=facts,
        graded_cases=tuple(graded),
        calls_by_split=ubah.pop(
            "calls_by_split",
            {SplitTag.TEST: CallCounts(total=4, failed=1), SplitTag.DEV: CallCounts(2, 2)},
        ),
    )


class TestMeasurementKind:
    def test_automatic_session_is_an_automatic_measurement(self):
        # Act and Assert
        assert _rekaman(session_type=SessionType.AUTOMATIC).measurement_kind is (
            MeasurementKind.AUTOMATIC
        )

    def test_manual_session_is_a_manual_measurement(self):
        # Act and Assert
        assert _rekaman(session_type=SessionType.MANUAL).measurement_kind is MeasurementKind.MANUAL


class TestFactsForScope:
    @staticmethod
    def _kode(facts: SessionFacts) -> list[str]:
        return [kasus.case_code for kasus in facts.scored_cases]

    def test_test_scope_keeps_only_test_cases_as_scored_cases(self):
        # Arrange
        rekaman = _rekaman()

        # Act
        facts = facts_for_scope(rekaman, ScoreScope.TEST)

        # Assert
        assert self._kode(facts) == ["CASE-001", "CASE-003"]

    def test_dev_scope_keeps_only_dev_cases_as_scored_cases(self):
        # Arrange
        rekaman = _rekaman()

        # Act
        facts = facts_for_scope(rekaman, ScoreScope.DEV)

        # Assert
        assert self._kode(facts) == ["CASE-002"]

    def test_dev_test_scope_keeps_every_scored_case(self):
        # Arrange
        rekaman = _rekaman()

        # Act
        facts = facts_for_scope(rekaman, ScoreScope.DEV_TEST)

        # Assert
        assert self._kode(facts) == ["CASE-001", "CASE-002", "CASE-003"]

    def test_everything_but_scored_cases_is_left_as_it_was(self):
        # Arrange
        rekaman = _rekaman()

        # Act
        facts = facts_for_scope(rekaman, ScoreScope.TEST)

        # Assert
        assert facts.session_id == rekaman.facts.session_id
        assert facts.snapshot_cases == rekaman.facts.snapshot_cases
        assert facts.rubric_version_id == rekaman.facts.rubric_version_id

    def test_a_session_with_no_graded_cases_has_no_scored_cases(self):
        # Arrange
        rekaman = _rekaman(graded_cases=(), calls_by_split={})

        # Act
        facts = facts_for_scope(rekaman, ScoreScope.DEV_TEST)

        # Assert
        assert facts.scored_cases == ()


class TestCallsForSplit:
    @pytest.mark.parametrize(
        ("split", "diharapkan"),
        [
            (ScoreSplit.TEST, CallCounts(total=4, failed=1)),
            (ScoreSplit.DEV, CallCounts(total=2, failed=2)),
            (ScoreSplit.COMBINED, CallCounts(total=6, failed=3)),
        ],
    )
    def test_counts_calls_of_the_cases_in_that_split(self, split, diharapkan):
        # Arrange
        rekaman = _rekaman()

        # Act
        hasil = calls_for_split(rekaman, split)

        # Assert
        assert hasil == diharapkan

    def test_a_split_without_calls_counts_zero(self):
        # Arrange
        rekaman = _rekaman(calls_by_split={SplitTag.TEST: CallCounts(total=4, failed=1)})

        # Act
        hasil = calls_for_split(rekaman, ScoreSplit.DEV)

        # Assert
        assert hasil == CallCounts(total=0, failed=0)
