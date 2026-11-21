"""GET /reports/trend/pdf. PBI-17 SCRUM-84 SCRUM-333, diagram D4 langkah 8.

Pembaca sesi dan katalog produk diganti pembuat palsu yang sama dengan test GET
/reports/trend, jadi kedua endpoint membaca data yang identik. Jam diganti jam palsu
(tanggal dan nama berkas tetap), dan pembuat PDF diganti versi tanpa kompresi supaya
isinya bisa dicari, semuanya lewat dependency_overrides. Peran dipalsukan lewat as_role.

Yang diuji: bentuk response dan header, aturan peran yang sama dengan /trend (skor dev
tidak bocor), 404, 422, 401, dan angka di PDF sama dengan /trend untuk parameter yang
sama (pelajaran E69: setiap nilai yang disalin dites cocok dengan sumbernya).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.main import app
from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import RUBRIC_VERSION
from app.modules.reports.dependencies import (
    get_now,
    get_product_catalog,
    get_session_record_reader,
    get_trend_pdf_renderer,
)
from app.modules.reports.models import ReportLanguage
from app.modules.reports.pdf_common import format_score
from app.modules.reports.schemas import TrendRead
from app.modules.reports.trend_pdf import TrendPdfRenderer
from app.shared.security import Role
from tests.modules.reports.test_report_generation import PembacaPalsu, _nilai, _ref, _rekaman
from tests.modules.reports.test_trend_service import KatalogPalsu

URL = "/reports/trend/pdf"
URL_JSON = "/reports/trend"
PRODUK = uuid.UUID("55555555-5555-5555-5555-555555555555")
RUBRIK_BARU = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
# 21 Nov 2026 10:00 WIB.
SEKARANG = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)


def _sesi(bulan: int, kasus=None, **ubah):
    kasus = kasus if kasus is not None else [_nilai(1, "0.83", "0.61"), _nilai(2, "0.41", "0.27")]
    return _rekaman(
        "Produk A",
        kasus,
        product_id=PRODUK,
        dibuat=datetime(2026, bulan, 5, tzinfo=UTC),
        **ubah,
    )


class RendererMata(TrendPdfRenderer):
    """Pembuat PDF asli (tanpa kompresi) yang juga mencatat TrendRead yang diterimanya."""

    def __init__(self) -> None:
        super().__init__(compress=False)
        self.diterima: list[tuple[TrendRead, ReportLanguage]] = []

    def render(self, trend, *, language, generated_at):
        self.diterima.append((trend, language))
        return super().render(trend, language=language, generated_at=generated_at)


@pytest.fixture
def pasang():
    dipasang = {}

    def _pasang(*rekaman, katalog=None):
        pembaca = PembacaPalsu(*rekaman)
        renderer = RendererMata()
        app.dependency_overrides[get_session_record_reader] = lambda: pembaca
        app.dependency_overrides[get_product_catalog] = lambda: (
            katalog if katalog is not None else KatalogPalsu({PRODUK: "Produk A"})
        )
        app.dependency_overrides[get_trend_pdf_renderer] = lambda: renderer
        app.dependency_overrides[get_now] = lambda: SEKARANG
        dipasang.update(pembaca=pembaca, renderer=renderer)
        return pembaca, renderer

    yield _pasang
    for dependency in (
        get_session_record_reader,
        get_product_catalog,
        get_trend_pdf_renderer,
        get_now,
    ):
        app.dependency_overrides.pop(dependency, None)


def _sel(teks: str) -> bytes:
    """Satu sel tabel PDF berisi persis teks ini (bukan potongan teks lain)."""
    return b"(" + teks.encode() + b") Tj"


class TestResponse:
    def test_returns_a_pdf_attachment_named_from_ids_and_choices(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10), _sesi(11))

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 200
        assert respons.headers["content-type"] == "application/pdf"
        assert respons.headers["content-disposition"] == (
            f'attachment; filename="tren-{PRODUK}-test-total-all-en-2026-11.pdf"'
        )
        assert respons.content.startswith(b"%PDF")

    def test_english_is_the_default_language(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert b"Score trend: Produk A" in respons.content
        assert b"Created 21 November 2026" in respons.content

    def test_language_id_gives_an_indonesian_pdf_and_file_name(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.VIEWER).get(
            URL, params={"product_id": str(PRODUK), "language": "id"}
        )

        # Assert
        assert b"Tren skor Produk A" in respons.content
        assert respons.headers["content-disposition"].endswith('-id-2026-11.pdf"')

    def test_the_query_options_reach_the_pdf_and_the_file_name(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.ADMIN).get(
            URL,
            params={
                "product_id": str(PRODUK),
                "metric": "llm",
                "range": "12m",
                "score_scope": "dev_test",
            },
        )

        # Assert
        assert b"Metric: LLM judge score" in respons.content
        assert b"Range: Last 12 months" in respons.content
        assert b"Case type: Dev + test combined" in respons.content
        assert respons.headers["content-disposition"] == (
            f'attachment; filename="tren-{PRODUK}-dev_test-llm-12m-en-2026-11.pdf"'
        )

    def test_a_product_without_sessions_still_gives_a_pdf(self, as_role, pasang):
        # Arrange
        pasang()

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 200
        assert b"No eligible session for this product in this range." in respons.content

    def test_no_shared_cases_gives_a_pdf_with_the_warning(self, as_role, pasang):
        # Arrange: kasus berbeda, snapshot sama, jadi tidak ada kasus yang sama.
        a = _sesi(9, kasus=[_nilai(1, "0.8", "0.6")], snapshot_tambahan=(_ref(2),))
        b = _sesi(10, kasus=[_nilai(2, "0.8", "0.6")], snapshot_tambahan=(_ref(1),))
        pasang(a, b)

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code == 200
        assert b"No case is shared by the sessions of a line" in respons.content


class TestSameNumbersAsTheTrend:
    @pytest.mark.parametrize("metric", ["total", "deterministic", "llm"])
    @pytest.mark.parametrize("language", ["en", "id"])
    def test_the_pdf_receives_exactly_the_trend_of_the_json_endpoint(
        self, as_role, pasang, metric, language
    ):
        # Arrange: tiga bulan, rubrik baru di November memutus garis.
        _, renderer = pasang(_sesi(9), _sesi(10), _sesi(11, rubrik=RUBRIK_BARU))
        client = as_role(Role.VIEWER)
        params = {"product_id": str(PRODUK), "metric": metric}

        # Act
        json = client.get(URL_JSON, params=params).json()
        pdf = client.get(URL, params={**params, "language": language}).content

        # Assert: objek yang dirender sama persis dengan JSON tren, tidak dihitung ulang.
        diterima, bahasa = renderer.diterima[-1]
        assert diterima.model_dump(mode="json") == json
        assert bahasa is ReportLanguage(language)
        # Dan setiap nilai serta jumlah kasus dari JSON benar-benar tercetak di tabel PDF.
        for titik in json["points"]:
            nilai = None if titik["value"] is None else Decimal(str(titik["value"]))
            assert _sel(format_score(nilai, ReportLanguage(language))) in pdf
            assert _sel(str(titik["case_count"])) in pdf
        assert json["points"][2]["breaks"] == [RUBRIC_VERSION]

    def test_the_default_values_are_the_ones_of_the_json_endpoint(self, as_role, pasang):
        # Arrange
        pasang(_sesi(10))
        client = as_role(Role.VIEWER)

        # Act
        json = client.get(URL_JSON, params={"product_id": str(PRODUK)}).json()
        pdf = client.get(URL, params={"product_id": str(PRODUK)}).content

        # Assert: total test semua = (0.83 + 0.41 + 0.61 + 0.27) / 4 = 0.53.
        assert Decimal(str(json["points"][0]["value"])) == Decimal("0.53")
        assert _sel("0.53") in pdf
        assert _sel("2") in pdf


class TestAccess:
    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", ["dev", "dev_test"])
    def test_viewer_and_contributor_get_403_and_nothing_is_read(self, as_role, pasang, role, scope):
        # Arrange
        pembaca, renderer = pasang(_sesi(10, kasus=[_nilai(2, "0.10", "0.30", SplitTag.DEV)]))

        # Act
        respons = as_role(role).get(URL, params={"product_id": str(PRODUK), "score_scope": scope})

        # Assert: ditolak sebelum sesi dibaca dan sebelum PDF dibuat (AC1, AC12).
        assert respons.status_code == 403
        assert respons.json()["code"] == "TREND_SCOPE_FORBIDDEN"
        assert pembaca.dipanggil == []
        assert renderer.diterima == []
        assert respons.headers["content-type"].startswith("application/json")

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.DEVELOPER_AI])
    def test_admin_and_developer_ai_get_the_dev_trend_marked_internal(self, as_role, pasang, role):
        # Arrange
        pasang(_sesi(10, kasus=[_nilai(2, "0.10", "0.30", SplitTag.DEV)]))

        # Act
        respons = as_role(role).get(
            URL, params={"product_id": str(PRODUK), "score_scope": "dev", "language": "id"}
        )

        # Assert
        assert respons.status_code == 200
        assert b"Tren berisi skor dev, hanya untuk internal dan tidak diterbitkan." in (
            respons.content
        )
        assert _sel("0,20") in respons.content

    def test_a_viewer_never_receives_a_dev_score_in_the_default_trend(self, as_role, pasang):
        # Arrange: satu kasus test 0.70 dan satu kasus dev 0.20.
        pasang(
            _sesi(
                10,
                kasus=[
                    _nilai(1, "0.80", "0.60", SplitTag.TEST),
                    _nilai(2, "0.10", "0.30", SplitTag.DEV),
                ],
            )
        )

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert _sel("0.70") in respons.content
        assert _sel("0.45") not in respons.content
        assert b"dev scores" not in respons.content

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
            {"product_id": str(PRODUK), "language": "fr"},
        ],
    )
    def test_invalid_query_is_422(self, as_role, pasang, params):
        # Arrange
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.ADMIN).get(URL, params=params)

        # Assert
        assert respons.status_code == 422

    def test_the_pdf_route_is_not_taken_for_a_report_id(self, as_role, pasang):
        # Arrange: /trend/pdf harus terdaftar sebelum /{report_id}/pdf dan /{report_id}.
        pasang(_sesi(10))

        # Act
        respons = as_role(Role.VIEWER).get(URL, params={"product_id": str(PRODUK)})

        # Assert
        assert respons.status_code != 422
        assert respons.headers["content-type"] == "application/pdf"
