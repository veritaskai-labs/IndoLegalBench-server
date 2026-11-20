"""Tren skor satu produk antar bulan. PBI-17 SCRUM-84 SCRUM-331.

AC1 sampai AC6, AC11, AC12, AC13. Murni: rekaman sesi dibuat langsung, tanpa database.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.models import ScoreScope
from app.modules.reports.trend import TrendMetric, TrendRange, build_trend
from app.modules.runs.models import SessionStatus
from tests.modules.reports.test_report_generation import _nilai, _rekaman

HARI_INI = date(2026, 11, 20)


def _tren(*rekaman, scope=ScoreScope.TEST, metric=TrendMetric.TOTAL, window=TrendRange.ALL):
    return build_trend(rekaman, scope=scope, metric=metric, window=window, today=HARI_INI)


def _sesi(bulan: int, hari: int = 5, nama: str = "Produk A", kasus=None, **ubah):
    kasus = kasus if kasus is not None else [_nilai(1, "0.80", "0.60")]
    return _rekaman(nama, kasus, dibuat=datetime(2026, bulan, hari, tzinfo=UTC), **ubah)


class TestMonthlyPoints:
    def test_one_eligible_session_gives_one_point_in_its_month(self):
        # Arrange
        sesi = _sesi(10)

        # Act
        tren = _tren(sesi)

        # Assert
        assert [(p.month, p.session_id) for p in tren.points] == [
            (date(2026, 10, 1), sesi.facts.session_id)
        ]
        assert tren.points[0].label == "Sesi Produk A"
        assert tren.warnings == ()

    def test_points_are_ordered_by_month_whatever_the_input_order(self):
        # Arrange
        november, agustus, oktober = _sesi(11), _sesi(8), _sesi(10)

        # Act
        tren = _tren(november, agustus, oktober)

        # Assert
        assert [p.month for p in tren.points] == [
            date(2026, 8, 1),
            date(2026, 10, 1),
            date(2026, 11, 1),
        ]

    def test_only_the_latest_session_of_a_month_becomes_its_point(self):
        # Arrange
        awal = _sesi(10, hari=3)
        akhir = _sesi(10, hari=25)
        tengah = _sesi(10, hari=14)

        # Act
        tren = _tren(awal, akhir, tengah)

        # Assert
        assert [p.session_id for p in tren.points] == [akhir.facts.session_id]

    def test_a_new_product_starts_at_its_first_measured_month(self):
        # Arrange: tidak ada sesi Agustus dan September.
        pertama = _sesi(10)
        kedua = _sesi(11)

        # Act
        tren = _tren(pertama, kedua)

        # Assert
        assert [p.month for p in tren.points] == [date(2026, 10, 1), date(2026, 11, 1)]

    def test_a_month_without_any_session_has_no_point(self):
        # Arrange
        agustus, oktober = _sesi(8), _sesi(10)

        # Act
        tren = _tren(agustus, oktober)

        # Assert
        assert date(2026, 9, 1) not in [p.month for p in tren.points]

    def test_no_sessions_gives_an_empty_trend_without_warning(self):
        # Act
        tren = _tren()

        # Assert
        assert tren.points == ()
        assert tren.warnings == ()


class TestEligibility:
    @pytest.mark.parametrize(
        "status",
        [SessionStatus.CANCELLED, SessionStatus.FAILED, SessionStatus.FINISHED_WITH_FAILURES],
    )
    def test_a_session_that_is_not_finished_never_becomes_a_point(self, status):
        # Arrange
        layak = _sesi(10)
        tidak_layak = _sesi(11, status=status)

        # Act
        tren = _tren(layak, tidak_layak)

        # Assert
        assert [p.session_id for p in tren.points] == [layak.facts.session_id]

    def test_an_ungraded_session_never_becomes_a_point(self):
        # Arrange
        belum_dinilai = _sesi(10, rubrik=None)

        # Act
        tren = _tren(belum_dinilai)

        # Assert
        assert tren.points == ()

    def test_a_newer_ineligible_session_does_not_hide_the_eligible_one_in_its_month(self):
        # Arrange
        layak = _sesi(10, hari=3)
        batal_lebih_baru = _sesi(10, hari=28, status=SessionStatus.CANCELLED)

        # Act
        tren = _tren(layak, batal_lebih_baru)

        # Assert
        assert [p.session_id for p in tren.points] == [layak.facts.session_id]

    def test_a_session_without_a_creation_time_cannot_be_placed_in_a_month(self):
        # Arrange
        tanpa_waktu = _sesi(10)
        tanpa_waktu = type(tanpa_waktu)(
            facts=tanpa_waktu.facts,
            graded_cases=tanpa_waktu.graded_cases,
            calls_by_split=tanpa_waktu.calls_by_split,
            label=tanpa_waktu.label,
            created_at=None,
        )

        # Act
        tren = _tren(tanpa_waktu)

        # Assert
        assert tren.points == ()

    def test_a_naive_creation_time_is_read_as_utc(self):
        # Arrange: SQLite mengembalikan datetime tanpa zona.
        sesi = _rekaman("Produk A", [_nilai(1, "0.8", "0.6")], dibuat=datetime(2026, 10, 31, 23))

        # Act
        tren = _tren(sesi)

        # Assert
        assert [p.month for p in tren.points] == [date(2026, 10, 1)]


class TestMetric:
    def test_total_is_the_plain_average_of_both_paths(self):
        # Arrange
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60")])

        # Act
        tren = _tren(sesi, metric=TrendMetric.TOTAL)

        # Assert
        assert tren.points[0].value == Decimal("0.70")

    def test_deterministic_metric_draws_only_the_deterministic_path(self):
        # Arrange
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60")])

        # Act
        tren = _tren(sesi, metric=TrendMetric.DETERMINISTIC)

        # Assert
        assert tren.points[0].value == Decimal("0.80")

    def test_llm_metric_draws_only_the_llm_judge_path(self):
        # Arrange
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60")])

        # Act
        tren = _tren(sesi, metric=TrendMetric.LLM)

        # Assert
        assert tren.points[0].value == Decimal("0.60")

    def test_total_is_empty_not_zero_when_one_path_has_no_score(self):
        # Arrange
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", None)])

        # Act
        total = _tren(sesi, metric=TrendMetric.TOTAL)
        deterministik = _tren(sesi, metric=TrendMetric.DETERMINISTIC)

        # Assert
        assert total.points[0].value is None
        assert deterministik.points[0].value == Decimal("0.80")

    def test_the_value_is_rounded_to_two_decimals_only_at_the_end(self):
        # Arrange: rata-rata det = 2/3, llm = 1/3; total = 0.5 bila tidak dibulatkan dulu.
        kasus = [_nilai(1, "1", "0"), _nilai(2, "1", "0"), _nilai(3, "0", "1")]
        sesi = _sesi(10, kasus=kasus)

        # Act
        tren = _tren(sesi, metric=TrendMetric.DETERMINISTIC)

        # Assert
        assert tren.points[0].value == Decimal("0.67")


class TestCaseTypeFilter:
    @pytest.fixture
    def campuran(self):
        return _sesi(
            10,
            kasus=[
                _nilai(1, "0.80", "0.60", SplitTag.TEST),
                _nilai(2, "0.20", "0.20", SplitTag.DEV),
            ],
        )

    def test_test_only_ignores_dev_cases(self, campuran):
        # Act
        tren = _tren(campuran, scope=ScoreScope.TEST)

        # Assert
        assert tren.points[0].value == Decimal("0.70")

    def test_dev_only_ignores_test_cases(self, campuran):
        # Act
        tren = _tren(campuran, scope=ScoreScope.DEV)

        # Assert
        assert tren.points[0].value == Decimal("0.20")

    def test_all_averages_dev_and_test_cases_together(self, campuran):
        # Act
        tren = _tren(campuran, scope=ScoreScope.DEV_TEST)

        # Assert: det (0.8 + 0.2) / 2 = 0.5 dan llm (0.6 + 0.2) / 2 = 0.4.
        assert tren.points[0].value == Decimal("0.45")

    def test_a_session_without_cases_in_the_scope_has_an_empty_value(self):
        # Arrange: hanya kasus test, tetapi yang diminta skor dev.
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60", SplitTag.TEST)])

        # Act
        tren = _tren(sesi, scope=ScoreScope.DEV)

        # Assert
        assert tren.points[0].value is None
