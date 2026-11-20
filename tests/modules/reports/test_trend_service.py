"""Layanan tren satu produk. PBI-17 SCRUM-84 SCRUM-331.

AC1, AC2, AC3, AC11, AC12, AC13. Pembaca sesi dan katalog produk diganti pembuat palsu.
"""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import SUITE_VERSION
from app.modules.reports.models import ScoreScope
from app.modules.reports.trend import TrendMetric, TrendRange
from app.modules.reports.trend_policy import TREND_SCOPE_FORBIDDEN
from app.modules.reports.trend_service import TREND_PRODUCT_NOT_FOUND, TrendService
from app.shared.exceptions import ForbiddenError, NotFoundError
from app.shared.security import Role
from tests.modules.reports.test_report_generation import PembacaPalsu, _nilai, _rekaman

HARI_INI = date(2026, 11, 20)
PRODUK = uuid.UUID("55555555-5555-5555-5555-555555555555")
PRODUK_LAIN = uuid.UUID("66666666-6666-6666-6666-666666666666")
SUITE_LAIN = uuid.UUID("77777777-7777-7777-7777-777777777777")


class KatalogPalsu:
    def __init__(self, nama: dict[uuid.UUID, str] | None = None) -> None:
        self._nama = nama or {}

    def name_of(self, product_id):
        return self._nama.get(product_id)


def _sesi(bulan: int, produk: uuid.UUID = PRODUK, kasus=None, **ubah):
    kasus = kasus if kasus is not None else [_nilai(1, "0.80", "0.60")]
    return _rekaman(
        "Produk A",
        kasus,
        product_id=produk,
        dibuat=datetime(2026, bulan, 5, tzinfo=UTC),
        **ubah,
    )


def _layanan(*rekaman, katalog=None):
    pembaca = PembacaPalsu(*rekaman)
    katalog = katalog or KatalogPalsu({PRODUK: "Produk A"})
    return TrendService(pembaca, katalog, today=lambda: HARI_INI), pembaca


class TestGetTrend:
    def test_returns_the_trend_of_the_requested_product_only(self):
        # Arrange
        milik, lain = _sesi(10), _sesi(10, produk=PRODUK_LAIN)
        layanan, _ = _layanan(milik, lain)

        # Act
        hasil = layanan.get_trend(role=Role.VIEWER, product_id=PRODUK)

        # Assert
        assert hasil.product_id == PRODUK
        assert hasil.product_name == "Produk A"
        assert [p.session_id for p in hasil.trend.points] == [milik.facts.session_id]

    def test_echoes_the_chosen_scope_metric_and_range(self):
        # Arrange
        layanan, _ = _layanan(_sesi(10))

        # Act
        hasil = layanan.get_trend(
            role=Role.ADMIN,
            product_id=PRODUK,
            scope=ScoreScope.DEV_TEST,
            metric=TrendMetric.LLM,
            window=TrendRange.SIX_MONTHS,
        )

        # Assert
        assert (hasil.scope, hasil.metric, hasil.window) == (
            ScoreScope.DEV_TEST,
            TrendMetric.LLM,
            TrendRange.SIX_MONTHS,
        )

    def test_defaults_to_the_official_test_total_over_all_months(self):
        # Arrange
        layanan, _ = _layanan(_sesi(1), _sesi(10))

        # Act
        hasil = layanan.get_trend(role=Role.VIEWER, product_id=PRODUK)

        # Assert
        assert (hasil.scope, hasil.metric, hasil.window) == (
            ScoreScope.TEST,
            TrendMetric.TOTAL,
            TrendRange.ALL,
        )
        assert len(hasil.trend.points) == 2

    def test_uses_the_clock_to_cut_the_range(self):
        # Arrange
        layanan, _ = _layanan(_sesi(1), _sesi(10))

        # Act
        hasil = layanan.get_trend(
            role=Role.VIEWER, product_id=PRODUK, window=TrendRange.THREE_MONTHS
        )

        # Assert
        assert [p.month for p in hasil.trend.points] == [date(2026, 10, 1)]

    def test_a_known_product_without_sessions_gives_an_empty_trend(self):
        # Arrange
        layanan, _ = _layanan()

        # Act
        hasil = layanan.get_trend(role=Role.VIEWER, product_id=PRODUK)

        # Assert
        assert hasil.product_name == "Produk A"
        assert hasil.trend.points == ()

    def test_reads_the_stored_results_once_without_any_new_measurement(self):
        # Arrange
        layanan, pembaca = _layanan(_sesi(9), _sesi(10))

        # Act
        layanan.get_trend(role=Role.VIEWER, product_id=PRODUK)

        # Assert: hanya satu pembacaan hasil tersimpan; tidak ada yang dijalankan ulang.
        assert len(pembaca.dipanggil) == 1


class TestRefusals:
    def test_an_unknown_product_is_not_found_and_reads_no_session(self):
        # Arrange
        layanan, pembaca = _layanan(_sesi(10), katalog=KatalogPalsu())

        # Act
        with pytest.raises(NotFoundError) as galat:
            layanan.get_trend(role=Role.ADMIN, product_id=PRODUK)

        # Assert
        assert galat.value.code == TREND_PRODUCT_NOT_FOUND
        assert pembaca.dipanggil == []

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", [ScoreScope.DEV, ScoreScope.DEV_TEST])
    def test_dev_scores_are_refused_before_any_session_is_read(self, role, scope):
        # Arrange
        layanan, pembaca = _layanan(_sesi(10))

        # Act
        with pytest.raises(ForbiddenError) as galat:
            layanan.get_trend(role=role, product_id=PRODUK, scope=scope)

        # Assert
        assert galat.value.code == TREND_SCOPE_FORBIDDEN
        assert pembaca.dipanggil == []

    def test_a_role_without_dev_rights_never_receives_dev_scores(self):
        # Arrange: sesi punya kasus test dan dev; Viewer hanya boleh melihat skor test.
        sesi = _sesi(
            10,
            kasus=[
                _nilai(1, "0.80", "0.60", SplitTag.TEST),
                _nilai(2, "0.00", "0.00", SplitTag.DEV),
            ],
        )
        layanan, _ = _layanan(sesi)

        # Act
        hasil = layanan.get_trend(role=Role.VIEWER, product_id=PRODUK)

        # Assert
        assert hasil.trend.points[0].value == Decimal("0.70")


class TestDevTrend:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    def test_admin_and_developer_ai_get_the_dev_trend_of_any_product(self, role):
        # Arrange
        dev = [_nilai(2, "0.10", "0.30", SplitTag.DEV)]
        layanan, _ = _layanan(_sesi(10, kasus=dev), _sesi(11, kasus=dev))

        # Act
        hasil = layanan.get_trend(role=role, product_id=PRODUK, scope=ScoreScope.DEV)

        # Assert
        assert [p.value for p in hasil.trend.points] == [Decimal("0.20"), Decimal("0.20")]

    def test_a_dev_trend_is_cut_when_the_suite_version_differs(self):
        # Arrange: syarat perbandingan sama seperti PBI-15 (AC13).
        dev = [_nilai(2, "0.10", "0.30", SplitTag.DEV)]
        lama = _sesi(10, kasus=dev)
        baru = _sesi(11, kasus=dev)
        baru = replace(baru, facts=replace(baru.facts, suite_ids=frozenset({SUITE_LAIN})))
        layanan, _ = _layanan(lama, baru)

        # Act
        hasil = layanan.get_trend(role=Role.DEVELOPER_AI, product_id=PRODUK, scope=ScoreScope.DEV)

        # Assert
        assert hasil.trend.points[1].breaks == (SUITE_VERSION,)
