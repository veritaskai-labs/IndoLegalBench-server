"""Tren skor satu produk antar bulan. PBI-17 SCRUM-84 SCRUM-331.

AC1 sampai AC6, AC11, AC12, AC13. Murni: rekaman sesi dibuat langsung, tanpa database.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    JUDGE_MODEL,
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    RUBRIC_VERSION,
    SUITE_VERSION,
    TRIAL_SESSION,
)
from app.modules.reports.models import ScoreScope
from app.modules.reports.report_scoring import GradedCase
from app.modules.reports.trend import TrendMetric, TrendRange, build_trend
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType
from tests.modules.reports.test_report_generation import _nilai, _ref, _rekaman

HARI_INI = date(2026, 11, 20)


def _tren(
    *rekaman,
    scope=ScoreScope.TEST,
    metric=TrendMetric.TOTAL,
    window=TrendRange.ALL,
    today=HARI_INI,
):
    return build_trend(rekaman, scope=scope, metric=metric, window=window, today=today)


def _sesi(bulan: int, hari: int = 5, nama: str = "Produk A", kasus=None, tahun: int = 2026, **ubah):
    kasus = kasus if kasus is not None else [_nilai(1, "0.80", "0.60")]
    return _rekaman(nama, kasus, dibuat=datetime(tahun, bulan, hari, tzinfo=UTC), **ubah)


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


RUBRIK_BARU = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _kasus_versi(nomor: int, versi: int, split: SplitTag = SplitTag.TEST) -> GradedCase:
    return GradedCase(
        ref=_ref(nomor, versi=versi, split=split),
        split=split,
        det=Decimal("0.80"),
        llm=Decimal("0.60"),
    )


class TestVersionBreaks:
    def test_the_first_point_never_has_a_break(self):
        # Act
        tren = _tren(_sesi(10), _sesi(11))

        # Assert
        assert tren.points[0].breaks == ()

    def test_comparable_months_are_joined_without_a_break(self):
        # Act
        tren = _tren(_sesi(10), _sesi(11))

        # Assert
        assert tren.points[1].breaks == ()

    def test_a_new_rubric_version_breaks_the_line_at_that_month(self):
        # Arrange
        lama, baru = _sesi(10), _sesi(11, rubrik=RUBRIK_BARU)

        # Act
        tren = _tren(lama, baru)

        # Assert
        assert tren.points[1].breaks == (RUBRIC_VERSION,)

    def test_a_new_product_model_version_breaks_the_line(self):
        # Arrange
        lama, baru = _sesi(10, model="model-1"), _sesi(11, model="model-2")

        # Act
        tren = _tren(lama, baru)

        # Assert
        assert tren.points[1].breaks == (MODEL_VERSION_DIFFERS,)

    def test_a_new_case_version_breaks_the_line(self):
        # Arrange
        lama = _sesi(10, kasus=[_kasus_versi(1, versi=1)])
        baru = _sesi(11, kasus=[_kasus_versi(1, versi=2)])

        # Act
        tren = _tren(lama, baru)

        # Assert
        assert tren.points[1].breaks == (SUITE_VERSION,)

    def test_a_different_judge_model_breaks_the_line(self):
        # Arrange
        lama, baru = _sesi(10, judge_versi="judge-1"), _sesi(11, judge_versi="judge-2")

        # Act
        tren = _tren(lama, baru)

        # Assert
        assert tren.points[1].breaks == (JUDGE_MODEL,)

    def test_every_reason_for_the_break_is_listed(self):
        # Arrange
        lama = _sesi(10)
        baru = _sesi(11, rubrik=RUBRIK_BARU, model="model-2", judge_versi="judge-2")

        # Act
        tren = _tren(lama, baru)

        # Assert
        assert tren.points[1].breaks == (RUBRIC_VERSION, JUDGE_MODEL, MODEL_VERSION_DIFFERS)

    def test_a_break_compares_a_point_with_the_one_right_before_it(self):
        # Arrange: model berubah di Oktober saja; November sama dengan Oktober.
        tren = _tren(_sesi(9, model="m1"), _sesi(10, model="m2"), _sesi(11, model="m2"))

        # Assert
        assert [p.breaks for p in tren.points] == [(), (MODEL_VERSION_DIFFERS,), ()]

    def test_a_dev_case_change_does_not_break_a_test_only_trend(self):
        # Arrange
        lama = _sesi(10, kasus=[_kasus_versi(1, 1), _kasus_versi(2, 1, SplitTag.DEV)])
        baru = _sesi(11, kasus=[_kasus_versi(1, 1), _kasus_versi(2, 2, SplitTag.DEV)])

        # Act
        test_saja = _tren(lama, baru, scope=ScoreScope.TEST)
        dev_saja = _tren(lama, baru, scope=ScoreScope.DEV)

        # Assert
        assert test_saja.points[1].breaks == ()
        assert dev_saja.points[1].breaks == (SUITE_VERSION,)

    def test_breaks_apply_to_every_metric(self):
        # Arrange
        lama, baru = _sesi(10), _sesi(11, rubrik=RUBRIK_BARU)

        # Act
        hasil = {
            metrik: _tren(lama, baru, metric=metrik).points[1].breaks for metrik in TrendMetric
        }

        # Assert
        assert set(hasil.values()) == {(RUBRIC_VERSION,)}


class TestMarkers:
    def test_a_trial_session_is_marked(self):
        # Act
        tren = _tren(_sesi(10, purpose=SessionPurpose.TRIAL))

        # Assert
        assert tren.points[0].markers == (TRIAL_SESSION,)

    def test_a_manual_session_with_other_attempt_counts_is_marked(self):
        # Arrange
        a = _sesi(10, session_type=SessionType.MANUAL, status="finalized", repetisi=1)
        b = _sesi(11, session_type=SessionType.MANUAL, status="finalized", repetisi=3)

        # Act
        tren = _tren(a, b)

        # Assert
        assert [p.markers for p in tren.points] == [(REPETITIONS_DIFFER,), (REPETITIONS_DIFFER,)]

    def test_equal_attempt_counts_are_not_marked(self):
        # Arrange
        a = _sesi(10, session_type=SessionType.MANUAL, status="finalized", repetisi=2)
        b = _sesi(11, session_type=SessionType.MANUAL, status="finalized", repetisi=2)

        # Act
        tren = _tren(a, b)

        # Assert
        assert [p.markers for p in tren.points] == [(), ()]

    def test_an_automatic_session_with_other_attempt_counts_is_not_marked(self):
        # Arrange: penanda ini hanya untuk sesi manual (AC5).
        a, b = _sesi(10, repetisi=1), _sesi(11, repetisi=3)

        # Act
        tren = _tren(a, b)

        # Assert
        assert [p.markers for p in tren.points] == [(), ()]

    def test_a_manual_answer_range_over_the_limit_is_marked(self):
        # Arrange
        sesi = _sesi(
            10,
            session_type=SessionType.MANUAL,
            status="finalized",
            dari=datetime(2026, 10, 1, tzinfo=UTC),
            sampai=datetime(2026, 10, 20, tzinfo=UTC),
        )

        # Act
        tren = _tren(sesi)

        # Assert
        assert tren.points[0].markers == (MANUAL_ANSWER_RANGE_OVER_LIMIT,)


class TestSameCases:
    """Tiap sesi hanya menjawab sebagian kasus, tetapi snapshot suite-nya sama (tanpa putus)."""

    def test_only_cases_answered_in_every_session_of_the_line_are_scored(self):
        # Arrange: kasus 2 satu-satunya yang dijawab kedua sesi.
        oktober = _sesi(
            10,
            kasus=[_nilai(1, "1.00", "1.00"), _nilai(2, "0.50", "0.50")],
            snapshot_tambahan=(_ref(3),),
        )
        november = _sesi(
            11,
            kasus=[_nilai(2, "0.70", "0.70"), _nilai(3, "0.00", "0.00")],
            snapshot_tambahan=(_ref(1),),
        )

        # Act
        tren = _tren(oktober, november)

        # Assert
        assert [p.value for p in tren.points] == [Decimal("0.50"), Decimal("0.70")]
        assert tren.warnings == ()

    def test_the_same_case_rule_applies_to_every_metric(self):
        # Arrange
        oktober = _sesi(
            10,
            kasus=[_nilai(1, "1.00", "0.00"), _nilai(2, "0.50", "0.20")],
            snapshot_tambahan=(_ref(3),),
        )
        november = _sesi(
            11,
            kasus=[_nilai(2, "0.70", "0.40"), _nilai(3, "0.00", "0.00")],
            snapshot_tambahan=(_ref(1),),
        )

        # Act
        deterministik = _tren(oktober, november, metric=TrendMetric.DETERMINISTIC)
        llm = _tren(oktober, november, metric=TrendMetric.LLM)

        # Assert
        assert [p.value for p in deterministik.points] == [Decimal("0.50"), Decimal("0.70")]
        assert [p.value for p in llm.points] == [Decimal("0.20"), Decimal("0.40")]

    def test_no_shared_case_draws_no_value_and_warns(self):
        # Arrange
        oktober = _sesi(10, kasus=[_nilai(1, "0.80", "0.60")], snapshot_tambahan=(_ref(2),))
        november = _sesi(11, kasus=[_nilai(2, "0.70", "0.50")], snapshot_tambahan=(_ref(1),))

        # Act
        tren = _tren(oktober, november)

        # Assert
        assert [p.value for p in tren.points] == [None, None]
        assert tren.warnings == (NO_SHARED_CASES,)

    def test_a_single_session_is_scored_on_all_its_own_cases_without_warning(self):
        # Arrange
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60"), _nilai(2, "0.40", "0.20")])

        # Act
        tren = _tren(sesi)

        # Assert
        assert tren.points[0].value == Decimal("0.50")
        assert tren.warnings == ()

    def test_a_single_session_without_scores_is_empty_without_warning(self):
        # Act
        tren = _tren(_sesi(10, kasus=[_nilai(1, None, None)]))

        # Assert
        assert tren.points[0].value is None
        assert tren.warnings == ()

    def test_each_line_segment_keeps_its_own_shared_cases(self):
        # Arrange: rubrik baru memutus garis, jadi November tidak dibatasi kasus Oktober.
        september = _sesi(
            9,
            kasus=[_nilai(1, "1.00", "1.00"), _nilai(2, "0.50", "0.50")],
            snapshot_tambahan=(_ref(3),),
        )
        oktober = _sesi(
            10,
            kasus=[_nilai(2, "0.50", "0.50"), _nilai(3, "0.00", "0.00")],
            snapshot_tambahan=(_ref(1),),
        )
        november = _sesi(
            11,
            kasus=[_nilai(1, "1.00", "1.00"), _nilai(2, "0.00", "0.00"), _nilai(3, "0.00", "0.00")],
            rubrik=RUBRIK_BARU,
        )

        # Act
        tren = _tren(september, oktober, november)

        # Assert: September dan Oktober dibatasi kasus 2; November memakai ketiga kasusnya.
        assert [p.value for p in tren.points] == [
            Decimal("0.50"),
            Decimal("0.50"),
            Decimal("0.33"),
        ]
        assert tren.points[2].breaks == (RUBRIC_VERSION,)

    def test_a_segment_without_shared_cases_does_not_empty_another_segment(self):
        # Arrange: Agustus dan September tidak punya kasus sama; Oktober dan November punya.
        agustus = _sesi(8, kasus=[_nilai(1, "0.80", "0.80")], snapshot_tambahan=(_ref(2),))
        september = _sesi(9, kasus=[_nilai(2, "0.60", "0.60")], snapshot_tambahan=(_ref(1),))
        oktober = _sesi(10, kasus=[_nilai(5, "0.40", "0.40")], rubrik=RUBRIK_BARU)
        november = _sesi(11, kasus=[_nilai(5, "0.90", "0.90")], rubrik=RUBRIK_BARU)

        # Act
        tren = _tren(agustus, september, oktober, november)

        # Assert
        assert [p.value for p in tren.points] == [None, None, Decimal("0.40"), Decimal("0.90")]
        assert tren.warnings == (NO_SHARED_CASES,)

    def test_the_same_case_rule_follows_the_case_type_filter(self):
        # Arrange: kasus test sama, kasus dev berbeda; yang digambar hanya dev.
        oktober = _sesi(
            10,
            kasus=[_nilai(1, "0.80", "0.80"), _nilai(2, "0.10", "0.10", SplitTag.DEV)],
            snapshot_tambahan=(_ref(3, split=SplitTag.DEV),),
        )
        november = _sesi(
            11,
            kasus=[_nilai(1, "0.80", "0.80"), _nilai(3, "0.30", "0.30", SplitTag.DEV)],
            snapshot_tambahan=(_ref(2, split=SplitTag.DEV),),
        )

        # Act
        tren = _tren(oktober, november, scope=ScoreScope.DEV)

        # Assert
        assert [p.value for p in tren.points] == [None, None]
        assert tren.warnings == (NO_SHARED_CASES,)


class TestRange:
    @pytest.fixture
    def sepanjang_tahun(self):
        """Satu sesi per bulan dari November 2025 sampai November 2026 (HARI_INI 20 Nov 2026)."""
        return [_sesi(11, tahun=2025), _sesi(12, tahun=2025), *(_sesi(m) for m in range(1, 12))]

    def test_all_is_the_default_and_keeps_every_month(self, sepanjang_tahun):
        # Act
        tren = _tren(*sepanjang_tahun)

        # Assert
        assert len(tren.points) == 13
        assert tren.points[0].month == date(2025, 11, 1)

    @pytest.mark.parametrize(
        ("window", "first_month", "count"),
        [
            (TrendRange.THREE_MONTHS, date(2026, 9, 1), 3),
            (TrendRange.SIX_MONTHS, date(2026, 6, 1), 6),
            (TrendRange.TWELVE_MONTHS, date(2025, 12, 1), 12),
        ],
    )
    def test_a_range_keeps_the_current_month_and_the_calendar_months_before_it(
        self, sepanjang_tahun, window, first_month, count
    ):
        # Act
        tren = _tren(*sepanjang_tahun, window=window)

        # Assert
        assert tren.points[0].month == first_month
        assert tren.points[-1].month == date(2026, 11, 1)
        assert len(tren.points) == count

    def test_a_range_counts_calendar_months_across_a_new_year(self):
        # Arrange
        sesi = [
            _sesi(10, tahun=2026),
            _sesi(11, tahun=2026),
            _sesi(12, tahun=2026),
            _sesi(1, tahun=2027),
        ]

        # Act
        tren = _tren(*sesi, window=TrendRange.THREE_MONTHS, today=date(2027, 1, 15))

        # Assert
        assert [p.month for p in tren.points] == [
            date(2026, 11, 1),
            date(2026, 12, 1),
            date(2027, 1, 1),
        ]

    def test_a_range_with_no_session_inside_it_gives_an_empty_trend(self):
        # Act
        tren = _tren(_sesi(1), window=TrendRange.THREE_MONTHS)

        # Assert
        assert tren.points == ()
        assert tren.warnings == ()

    def test_cases_of_months_outside_the_range_do_not_limit_the_shared_cases(self):
        # Arrange: Agustus hanya menjawab kasus 1; di luar rentang 3 bulan.
        agustus = _sesi(8, kasus=[_nilai(1, "1.00", "1.00")], snapshot_tambahan=(_ref(2),))
        september = _sesi(9, kasus=[_nilai(1, "0.00", "0.00"), _nilai(2, "0.50", "0.50")])
        oktober = _sesi(10, kasus=[_nilai(1, "0.00", "0.00"), _nilai(2, "0.50", "0.50")])

        # Act
        semua = _tren(agustus, september, oktober)
        tiga_bulan = _tren(agustus, september, oktober, window=TrendRange.THREE_MONTHS)

        # Assert
        assert [p.value for p in semua.points] == [
            Decimal("1.00"),
            Decimal("0.00"),
            Decimal("0.00"),
        ]
        assert [p.value for p in tiga_bulan.points] == [Decimal("0.25"), Decimal("0.25")]

    def test_the_first_point_in_the_range_has_no_break_even_if_the_month_before_differs(self):
        # Arrange
        lama = _sesi(8, rubrik=RUBRIK_BARU)
        baru = _sesi(9)

        # Act
        tren = _tren(lama, baru, window=TrendRange.THREE_MONTHS)

        # Assert
        assert [p.breaks for p in tren.points] == [()]


class TestCaseCount:
    """SCRUM-333: jumlah kasus tiap segmen ikut dikirim (D4, alur p17), karena sesi baru bisa
    menggeser titik lama di segmen yang sama (keputusan SA di D4)."""

    def test_a_single_session_counts_all_its_own_cases(self):
        # Arrange
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60"), _nilai(2, "0.40", "0.20")])

        # Act
        tren = _tren(sesi)

        # Assert
        assert tren.points[0].case_count == 2

    def test_every_point_of_a_segment_counts_the_shared_cases_only(self):
        # Arrange: kasus 2 satu-satunya yang dijawab kedua sesi.
        oktober = _sesi(
            10,
            kasus=[_nilai(1, "1.00", "1.00"), _nilai(2, "0.50", "0.50")],
            snapshot_tambahan=(_ref(3),),
        )
        november = _sesi(
            11,
            kasus=[_nilai(2, "0.70", "0.70"), _nilai(3, "0.00", "0.00")],
            snapshot_tambahan=(_ref(1),),
        )

        # Act
        tren = _tren(oktober, november)

        # Assert
        assert [p.case_count for p in tren.points] == [1, 1]

    def test_each_segment_has_its_own_count_after_a_break(self):
        # Arrange: rubrik baru di November memulai segmen baru dengan tiga kasusnya sendiri.
        september = _sesi(
            9,
            kasus=[_nilai(1, "1.00", "1.00"), _nilai(2, "0.50", "0.50")],
            snapshot_tambahan=(_ref(3),),
        )
        oktober = _sesi(
            10,
            kasus=[_nilai(2, "0.50", "0.50"), _nilai(3, "0.00", "0.00")],
            snapshot_tambahan=(_ref(1),),
        )
        november = _sesi(
            11,
            kasus=[_nilai(1, "1.00", "1.00"), _nilai(2, "0.00", "0.00"), _nilai(3, "0.00", "0.00")],
            rubrik=RUBRIK_BARU,
        )

        # Act
        tren = _tren(september, oktober, november)

        # Assert
        assert [p.case_count for p in tren.points] == [1, 1, 3]

    def test_a_segment_without_shared_cases_counts_zero(self):
        # Arrange
        oktober = _sesi(10, kasus=[_nilai(1, "0.80", "0.60")], snapshot_tambahan=(_ref(2),))
        november = _sesi(11, kasus=[_nilai(2, "0.70", "0.50")], snapshot_tambahan=(_ref(1),))

        # Act
        tren = _tren(oktober, november)

        # Assert
        assert [p.case_count for p in tren.points] == [0, 0]
        assert tren.warnings == (NO_SHARED_CASES,)

    @pytest.mark.parametrize(
        ("scope", "jumlah"),
        [(ScoreScope.TEST, 2), (ScoreScope.DEV, 1), (ScoreScope.DEV_TEST, 3)],
    )
    def test_the_count_follows_the_case_type_filter(self, scope, jumlah):
        # Arrange: dua kasus test dan satu kasus dev.
        sesi = _sesi(
            10,
            kasus=[
                _nilai(1, "0.80", "0.60", SplitTag.TEST),
                _nilai(2, "0.40", "0.20", SplitTag.TEST),
                _nilai(3, "0.20", "0.20", SplitTag.DEV),
            ],
        )

        # Act
        tren = _tren(sesi, scope=scope)

        # Assert
        assert tren.points[0].case_count == jumlah

    def test_a_case_without_a_score_on_the_chosen_path_still_counts(self):
        # Arrange: kasus 2 belum punya skor LLM, tetapi tetap bagian dari dasar perbandingan.
        sesi = _sesi(10, kasus=[_nilai(1, "0.80", "0.60"), _nilai(2, "0.40", None)])

        # Act
        tren = _tren(sesi, metric=TrendMetric.LLM)

        # Assert
        assert tren.points[0].case_count == 2
