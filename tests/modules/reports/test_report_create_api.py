"""POST /reports: membuat laporan perbandingan. PBI-15 SCRUM-79 SCRUM-313.

AC1, AC2, AC6, AC7, AC9, AC14. Pembaca sesi diganti pembaca palsu; peran dipalsukan lewat
as_role. Yang diuji: bentuk request dan response, kode status, dan pesan galat.
"""

from decimal import Decimal

import pytest

from app.main import app
from app.modules.cases.models import SplitTag
from app.modules.reports.dependencies import get_session_record_reader
from app.modules.reports.report_scoring import GradedCase
from app.shared.security import Role
from tests.modules.reports.test_report_generation import PembacaPalsu, _nilai, _ref, _rekaman

URL = "/reports"


@pytest.fixture
def pasang_pembaca():
    def pasang(*rekaman):
        app.dependency_overrides[get_session_record_reader] = lambda: PembacaPalsu(*rekaman)

    yield pasang
    app.dependency_overrides.pop(get_session_record_reader, None)


@pytest.fixture
def dua(pasang_pembaca):
    a = _rekaman("Produk A", [_nilai(1, "0.80", "0.60"), _nilai(2, "0.60", "0.40")])
    b = _rekaman("Produk B", [_nilai(1, "0.40", "0.40"), _nilai(2, "0.20", "0.20")])
    pasang_pembaca(a, b)
    return a, b


def _isi(dua, **ubah) -> dict:
    a, b = dua
    return {
        "title": "Laporan November",
        "session_ids": [str(a.facts.session_id), str(b.facts.session_id)],
        "score_scope": "test",
    } | ubah


class TestCreated:
    def test_admin_creates_a_report_and_gets_it_back_with_ranks(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        assert respons.status_code == 201
        isi = respons.json()
        assert isi["warning"] is None
        laporan = isi["report"]
        assert laporan["title"] == "Laporan November"
        assert laporan["score_scope"] == "test"
        assert laporan["has_dev_scores"] is False
        assert laporan["case_basis_count"] == 2
        assert laporan["status"] == "saved"
        peringkat = {s["product_label"]: s["results"][0]["rank"] for s in laporan["sessions"]}
        assert peringkat == {"Produk A": 1, "Produk B": 2}

    def test_scores_are_decimals_with_two_digits_and_split_by_path(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        sesi_a = next(
            s for s in respons.json()["report"]["sessions"] if s["product_label"] == "Produk A"
        )
        (hasil,) = sesi_a["results"]
        assert hasil["score_split"] == "test"
        assert hasil["det_score"] == "0.70"
        assert hasil["llm_score"] == "0.50"
        assert hasil["total_score"] == "0.60"
        assert hasil["cases_basis_count"] == 2
        assert hasil["failure_rate"] == "0.00"

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR, Role.DEVELOPER_AI])
    def test_every_role_may_create_a_test_report(self, as_role, dua, role):
        # Arrange
        client = as_role(role)

        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        assert respons.status_code == 201

    def test_defaults_are_all_measurements_real_names_and_english(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        laporan = respons.json()["report"]
        assert laporan["measurement_filter"] == "all"
        assert laporan["product_label_mode"] == "real_name"
        assert laporan["language"] == "en"

    def test_the_response_never_exposes_case_content_or_session_names(self, as_role, dua):
        # Arrange
        client = as_role(Role.DEVELOPER_AI)

        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        assert "CASE-" not in respons.text
        assert "product_id" not in respons.text

    def test_admin_can_ask_for_codes_instead_of_names(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua, product_label_mode="code"))

        # Assert
        labels = {s["product_label"] for s in respons.json()["report"]["sessions"]}
        assert labels == {"Product A", "Product B"}

    def test_the_session_markers_come_back_as_code_and_detail(self, as_role, pasang_pembaca):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")], model="model-1")
        b = _rekaman("B", [_nilai(1, "0.5", "0.5")], model="model-2")
        pasang_pembaca(a, b)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi((a, b)))

        # Assert
        for sesi in respons.json()["report"]["sessions"]:
            assert sesi["markers"] == [{"code": "model_version_differs", "detail": None}]


class TestDevReports:
    def test_developer_ai_gets_dev_and_combined_rows_in_a_dev_plus_test_report(
        self, as_role, pasang_pembaca
    ):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5", SplitTag.DEV)])
        b = _rekaman("B", [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5", SplitTag.DEV)])
        pasang_pembaca(a, b)
        client = as_role(Role.DEVELOPER_AI)

        # Act
        respons = client.post(URL, json=_isi((a, b), score_scope="dev_test"))

        # Assert
        assert respons.status_code == 201
        laporan = respons.json()["report"]
        assert laporan["has_dev_scores"] is True
        pemisah = {r["score_split"] for r in laporan["sessions"][0]["results"]}
        assert pemisah == {"test", "dev", "combined"}

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", ["dev", "dev_test"])
    def test_viewer_and_contributor_get_403_for_dev_scores(self, as_role, dua, role, scope):
        # Arrange
        client = as_role(role)

        # Act
        respons = client.post(URL, json=_isi(dua, score_scope=scope))

        # Assert
        assert respons.status_code == 403
        assert respons.json()["code"] == "REPORT_SCOPE_FORBIDDEN"

    def test_only_admin_may_ask_for_codes(self, as_role, dua):
        # Arrange
        client = as_role(Role.DEVELOPER_AI)

        # Act
        respons = client.post(URL, json=_isi(dua, product_label_mode="code"))

        # Assert
        assert respons.status_code == 403
        assert respons.json()["code"] == "REPORT_LABEL_MODE_FORBIDDEN"


class TestRefused:
    def test_unknown_session_is_404_and_names_the_missing_ids(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)
        hilang = "99999999-9999-9999-9999-999999999999"
        isi = _isi(dua)
        isi["session_ids"].append(hilang)

        # Act
        respons = client.post(URL, json=isi)

        # Assert
        assert respons.status_code == 404
        assert respons.json()["code"] == "REPORT_SESSION_NOT_FOUND"
        assert respons.json()["missing"] == [hilang]

    def test_a_non_comparable_selection_is_409_with_every_reason(self, as_role, pasang_pembaca):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status="cancelled")
        pasang_pembaca(a, batal)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi((a, batal)))

        # Assert
        assert respons.status_code == 409
        isi = respons.json()
        assert isi["code"] == "REPORT_NOT_COMPARABLE"
        assert isi["ineligible"] == {str(batal.facts.session_id): ["SESSION_CANCELLED"]}
        assert isi["selection_errors"] == []
        assert isi["rejections"] == []

    def test_a_409_never_shows_test_case_codes_to_developer_ai(self, as_role, pasang_pembaca):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
        versi_dua = GradedCase(
            ref=_ref(1, versi=2), split=SplitTag.TEST, det=Decimal("0.5"), llm=Decimal("0.5")
        )
        b = _rekaman("B", [versi_dua])
        pasang_pembaca(a, b)
        client = as_role(Role.DEVELOPER_AI)

        # Act
        respons = client.post(URL, json=_isi((a, b)))

        # Assert
        assert respons.status_code == 409
        (tolakan,) = respons.json()["rejections"]
        assert tolakan["requirement"] == "SUITE_VERSION"
        assert "CASE-" not in respons.text

    def test_no_shared_cases_is_a_200_warning_without_a_report(self, as_role, pasang_pembaca):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")], snapshot_tambahan=(_ref(2),))
        b = _rekaman("B", [_nilai(2, "0.5", "0.5")], snapshot_tambahan=(_ref(1),))
        pasang_pembaca(a, b)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi((a, b)))

        # Assert
        assert respons.status_code == 200
        assert respons.json() == {"report": None, "warning": "NO_SHARED_CASES"}

    def test_without_a_session_the_answer_is_401(self, client, dua):
        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        assert respons.status_code == 401


class TestInvalidRequest:
    @pytest.mark.parametrize(
        "ubah",
        [
            {"title": ""},
            {"title": "   "},
            {"title": "​﻿"},
            {"title": "x" * 201},
            {"session_ids": []},
            {"session_ids": ["bukan-uuid"]},
            {"score_scope": "semua"},
            {"score_scope": None},
            {"measurement_filter": None},
            {"product_label_mode": None},
            {"language": "fr"},
            {"tidak_dikenal": 1},
        ],
    )
    def test_is_422(self, as_role, dua, ubah):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua, **ubah))

        # Assert
        assert respons.status_code == 422

    def test_a_missing_required_field_is_422(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)
        isi = _isi(dua)
        del isi["score_scope"]

        # Act
        respons = client.post(URL, json=isi)

        # Assert
        assert respons.status_code == 422

    def test_the_title_is_trimmed_before_it_is_saved(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua, title="  Judul rapi ​"))

        # Assert
        assert respons.status_code == 201
        assert respons.json()["report"]["title"] == "Judul rapi"
