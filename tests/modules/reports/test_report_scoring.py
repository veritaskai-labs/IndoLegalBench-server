"""Skor, tingkat kegagalan, dan peringkat laporan. PBI-15 SCRUM-79 SCRUM-313.

AC2, AC6. Murni, tanpa database.
"""

import uuid
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import CaseRef
from app.modules.reports.models import MeasurementKind, ScoreSplit
from app.modules.reports.report_scoring import (
    CallCounts,
    GradedCase,
    RankEntry,
    SplitScore,
    cases_for_split,
    failure_rate,
    rank_products,
    score_cases,
)
from app.modules.runs.score_aggregation import round_score


def _kasus(
    nomor: int,
    *,
    det: str | None,
    llm: str | None,
    split: SplitTag = SplitTag.TEST,
) -> GradedCase:
    return GradedCase(
        ref=CaseRef(
            case_id=uuid.UUID(f"{nomor:032x}"),
            case_version_id=uuid.UUID(f"{nomor:016x}{1:016x}"),
            case_code=f"CASE-{nomor:03d}",
            split_tag=split.value,
        ),
        split=split,
        det=None if det is None else Decimal(det),
        llm=None if llm is None else Decimal(llm),
    )


def _entri(label: str, det: str | None, llm: str | None) -> RankEntry:
    return RankEntry(
        session_id=uuid.uuid4(),
        label=label,
        score=SplitScore(
            det=None if det is None else Decimal(det),
            llm=None if llm is None else Decimal(llm),
        ),
    )


class TestScoreCases:
    def test_averages_deterministic_and_llm_separately(self):
        # Arrange
        kasus = [_kasus(1, det="0.50", llm="0.00"), _kasus(2, det="1.00", llm="1.00")]

        # Act
        skor = score_cases(kasus)

        # Assert
        assert skor.det == Decimal("0.75")
        assert skor.llm == Decimal("0.5")

    def test_total_is_the_plain_mean_of_deterministic_and_llm(self):
        # Arrange
        kasus = [_kasus(1, det="0.50", llm="0.00"), _kasus(2, det="1.00", llm="1.00")]

        # Act
        skor = score_cases(kasus)

        # Assert
        assert skor.total == Decimal("0.625")

    def test_keeps_full_precision_so_rounding_happens_only_when_shown(self):
        # Arrange
        kasus = [_kasus(1, det="0.01", llm="0.50"), _kasus(2, det="0.02", llm="0.50")]

        # Act
        skor = score_cases(kasus)

        # Assert
        assert skor.det == Decimal("0.015")
        assert round_score(skor.det) == Decimal("0.02")

    def test_missing_scores_are_skipped_not_counted_as_zero(self):
        # Arrange
        kasus = [_kasus(1, det="0.80", llm=None), _kasus(2, det=None, llm="0.40")]

        # Act
        skor = score_cases(kasus)

        # Assert
        assert skor.det == Decimal("0.80")
        assert skor.llm == Decimal("0.40")

    def test_total_is_empty_when_one_path_has_no_score(self):
        # Arrange
        kasus = [_kasus(1, det="0.80", llm=None)]

        # Act
        skor = score_cases(kasus)

        # Assert
        assert skor.det == Decimal("0.80")
        assert skor.llm is None
        assert skor.total is None

    def test_no_cases_gives_no_scores(self):
        # Act
        skor = score_cases([])

        # Assert
        assert skor == SplitScore(det=None, llm=None)
        assert skor.total is None


class TestCasesForSplit:
    @pytest.fixture
    def campuran(self):
        return [
            _kasus(1, det="0.1", llm="0.1", split=SplitTag.TEST),
            _kasus(2, det="0.2", llm="0.2", split=SplitTag.DEV),
            _kasus(3, det="0.3", llm="0.3", split=SplitTag.TEST),
        ]

    def test_test_split_keeps_only_test_cases(self, campuran):
        # Act
        hasil = cases_for_split(campuran, ScoreSplit.TEST)

        # Assert
        assert [k.ref.case_code for k in hasil] == ["CASE-001", "CASE-003"]

    def test_dev_split_keeps_only_dev_cases(self, campuran):
        # Act
        hasil = cases_for_split(campuran, ScoreSplit.DEV)

        # Assert
        assert [k.ref.case_code for k in hasil] == ["CASE-002"]

    def test_combined_split_keeps_every_case(self, campuran):
        # Act
        hasil = cases_for_split(campuran, ScoreSplit.COMBINED)

        # Assert
        assert [k.ref.case_code for k in hasil] == ["CASE-001", "CASE-002", "CASE-003"]


class TestFailureRate:
    def test_manual_products_have_no_failure_rate(self):
        # Act
        hasil = failure_rate(MeasurementKind.MANUAL, CallCounts(total=10, failed=4))

        # Assert
        assert hasil is None

    def test_is_failed_calls_over_all_calls_as_two_decimal_percent(self):
        # Act
        hasil = failure_rate(MeasurementKind.AUTOMATIC, CallCounts(total=3, failed=1))

        # Assert
        assert hasil == Decimal("33.33")

    def test_rounds_half_up(self):
        # Act
        hasil = failure_rate(MeasurementKind.AUTOMATIC, CallCounts(total=3, failed=2))

        # Assert
        assert hasil == Decimal("66.67")

    def test_no_calls_means_zero_not_an_error(self):
        # Act
        hasil = failure_rate(MeasurementKind.AUTOMATIC, CallCounts(total=0, failed=0))

        # Assert
        assert hasil == Decimal("0.00")

    def test_every_call_failed_is_one_hundred(self):
        # Act
        hasil = failure_rate(MeasurementKind.AUTOMATIC, CallCounts(total=5, failed=5))

        # Assert
        assert hasil == Decimal("100.00")

    @pytest.mark.parametrize(
        "calls",
        [CallCounts(total=2, failed=3), CallCounts(total=-1, failed=0), CallCounts(2, -1)],
    )
    def test_impossible_counts_are_rejected(self, calls):
        # Act and Assert
        with pytest.raises(ValueError):
            failure_rate(MeasurementKind.AUTOMATIC, calls)


class TestRankProducts:
    def test_higher_total_ranks_first(self):
        # Arrange
        rendah = _entri("B", det="0.40", llm="0.40")
        tinggi = _entri("A", det="0.90", llm="0.90")

        # Act
        peringkat = rank_products([rendah, tinggi])

        # Assert
        assert peringkat[tinggi.session_id] == 1
        assert peringkat[rendah.session_id] == 2

    def test_equal_total_is_broken_by_higher_llm_score(self):
        # Arrange
        det_tinggi = _entri("A", det="0.80", llm="0.40")
        llm_tinggi = _entri("B", det="0.40", llm="0.80")

        # Act
        peringkat = rank_products([det_tinggi, llm_tinggi])

        # Assert
        assert peringkat[llm_tinggi.session_id] == 1
        assert peringkat[det_tinggi.session_id] == 2

    def test_full_tie_is_broken_by_label_alphabetically_and_ranks_stay_unique(self):
        # Arrange
        c = _entri("Charlie", det="0.60", llm="0.60")
        a = _entri("Alfa", det="0.60", llm="0.60")
        b = _entri("Bravo", det="0.60", llm="0.60")

        # Act
        peringkat = rank_products([c, a, b])

        # Assert
        assert sorted(peringkat.values()) == [1, 2, 3]
        assert (peringkat[a.session_id], peringkat[b.session_id], peringkat[c.session_id]) == (
            1,
            2,
            3,
        )

    def test_product_without_total_gets_no_rank_and_does_not_take_a_place(self):
        # Arrange
        tanpa_skor = _entri("A", det="0.90", llm=None)
        ada_skor = _entri("B", det="0.10", llm="0.10")

        # Act
        peringkat = rank_products([tanpa_skor, ada_skor])

        # Assert
        assert peringkat[tanpa_skor.session_id] is None
        assert peringkat[ada_skor.session_id] == 1

    def test_ranks_by_unrounded_total(self):
        # Arrange: keduanya tampil 0.50 setelah dibulatkan, tetapi mentahnya beda.
        sedikit_lebih_tinggi = _entri("Z", det="0.5040", llm="0.5040")
        sedikit_lebih_rendah = _entri("A", det="0.4960", llm="0.4960")

        # Act
        peringkat = rank_products([sedikit_lebih_rendah, sedikit_lebih_tinggi])

        # Assert
        assert peringkat[sedikit_lebih_tinggi.session_id] == 1
        assert peringkat[sedikit_lebih_rendah.session_id] == 2

    def test_no_entries_gives_no_ranks(self):
        # Act
        peringkat = rank_products([])

        # Assert
        assert peringkat == {}
