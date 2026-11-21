"""GET /reports/trend. PBI-17 SCRUM-84 SCRUM-331.

Pembaca sesi dan katalog produk diganti pembuat palsu; peran dipalsukan lewat as_role. Yang
diuji: bentuk response, kode status, validasi query, dan skor dev tidak bocor.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.main import app
from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import RUBRIC_VERSION
from app.modules.reports.dependencies import get_product_catalog, get_session_record_reader
from app.shared.security import Role
from tests.modules.reports.test_report_generation import PembacaPalsu, _nilai, _ref, _rekaman
from tests.modules.reports.test_trend_service import KatalogPalsu

URL = "/reports/trend"
PRODUK = uuid.UUID("55555555-5555-5555-5555-555555555555")
RUBRIK_BARU = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _sesi(bulan: int, kasus=None, **ubah):
    kasus = kasus if kasus is not None else [_nilai(1, "0.80", "0.60")]
    return _rekaman(
        "Produk A",
        kasus,
        product_id=PRODUK,
        dibuat=datetime(2026, bulan, 5, tzinfo=UTC),
        **ubah,
    )


@pytest.fixture
def pasang():
    def _pasang(*rekaman, katalog=None):
        app.dependency_overrides[get_session_record_reader] = lambda: PembacaPalsu(*rekaman)
        app.dependency_overrides[get_product_catalog] = lambda: (
            katalog if katalog is not None else KatalogPalsu({PRODUK: "Produk A"})
        )

    yield _pasang
    app.dependency_overrides.pop(get_session_record_reader, None)
    app.dependency_overrides.pop(get_product_catalog, None)


class TestTrendResponse:
    def test_returns_the_monthly_points_of_the_product(self, as_role, pasang):
        # Arrange
        pasang(_sesi(9), _sesi(10, rubrik=RUBRIK_BARU))
        client = as_role(Role.VIEWER)

        # Act
        respons = client.get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 200
        isi = respons.json()
        assert isi["product_id"] == str(PRODUK)
        assert isi["product_name"] == "Produk A"
        assert (isi["score_scope"], isi["metric"], isi["range"]) == ("test", "total", "all")
        assert [p["month"] for p in isi["points"]] == ["2026-09-01", "2026-10-01"]
        assert [Decimal(str(p["value"])) for p in isi["points"]] == [Decimal("0.70")] * 2
        assert [p["breaks"] for p in isi["points"]] == [[], [RUBRIC_VERSION]]
        assert isi["points"][0]["label"] == "Sesi Produk A"
        assert [p["case_count"] for p in isi["points"]] == [1, 1]
        assert isi["warnings"] == []

    def test_a_point_does_not_expose_the_session_id(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert set(respons.json()["points"][0]) == {
            "month",
            "label",
            "value",
            "breaks",
            "markers",
            "case_count",
        }

    def test_the_query_options_are_applied(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10, kasus=[_nilai(1, "0.80", "0.60")]))
        client = as_role(Role.ADMIN)

        # Act
        respons = client.get(
            URL, params={"product_id": str(PRODUK), "metric": "llm", "range": "12m"}
        )

        # Assert
        isi = respons.json()
        assert (isi["metric"], isi["range"]) == ("llm", "12m")
        assert Decimal(str(isi["points"][0]["value"])) == Decimal("0.60")

    def test_a_product_without_sessions_gives_an_empty_trend(self, as_role, pasang):
        # Arrange
        pasang()

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 200
        assert respons.json()["points"] == []

    def test_no_shared_cases_returns_the_warning_and_no_values(self, as_role, pasang):
        # Arrange
        a = _sesi(9, kasus=[_nilai(1, "0.8", "0.6")], snapshot_tambahan=(_ref(2),))
        b = _sesi(10, kasus=[_nilai(2, "0.8", "0.6")], snapshot_tambahan=(_ref(1),))
        pasang(a, b)

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        isi = respons.json()
        assert isi["warnings"] == ["NO_SHARED_CASES"]
        assert [p["value"] for p in isi["points"]] == [None, None]


class TestAccess:
    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", ["dev", "dev_test"])
    def test_viewer_and_contributor_get_403_for_dev_scores(self, as_role, pasang, role, scope):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(role).get(URL, params={"product_id": str(PRODUK), "score_scope": scope})

        # Assert
        assert respons.status_code == 403
        assert respons.json()["code"] == "TREND_SCOPE_FORBIDDEN"

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    def test_admin_and_developer_ai_get_the_dev_trend(self, as_role, pasang, role):
        # Arrange
        pasang(_sesi(10, kasus=[_nilai(2, "0.10", "0.30", SplitTag.DEV)]))

        # Act
        respons = as_role(role).get(URL, params={"product_id": str(PRODUK), "score_scope": "dev"})

        # Assert
        assert respons.status_code == 200
        assert Decimal(str(respons.json()["points"][0]["value"])) == Decimal("0.20")

    def test_a_viewer_never_receives_a_dev_score_in_the_default_trend(self, as_role, pasang):
        # Arrange
        pasang(
            _sesi(
                10,
                kasus=[
                    _nilai(1, "0.80", "0.60", SplitTag.TEST),
                    _nilai(2, "0.00", "0.00", SplitTag.DEV),
                ],
            )
        )

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert Decimal(str(respons.json()["points"][0]["value"])) == Decimal("0.70")

    def test_without_a_session_the_answer_is_401(self, client, pasang):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = client.get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 401


class TestErrors:
    def test_an_unknown_product_is_404(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10), katalog=KatalogPalsu())

        # Act
        respons = as_role(Role.ADMIN).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 404
        assert respons.json()["code"] == "TREND_PRODUCT_NOT_FOUND"

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"product_id": "bukan-uuid"},
            {"product_id": str(PRODUK), "metric": "rata-rata"},
            {"product_id": str(PRODUK), "range": "24m"},
            {"product_id": str(PRODUK), "score_scope": "semua"},
        ],
    )
    def test_invalid_query_is_422(self, as_role, pasang, params):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.ADMIN).get(URL, params=params)

        # Assert
        assert respons.status_code == 422
