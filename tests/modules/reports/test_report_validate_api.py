"""POST /reports/validate: validasi langsung pilihan sesi. PBI-15 SCRUM-79 SCRUM-362.

AC1, AC4, AC5, AC13, AC14. Pembaca sesi diganti pembaca palsu; peran dipalsukan lewat as_role.
Yang diuji: bentuk request dan response, kode status, peran, dan kesamaan alasan dengan
POST /reports (server tidak percaya hasil validate dari FE).
"""

import uuid
from datetime import timedelta

import pytest

from app.main import app
from app.modules.cases.models import SplitTag
from app.modules.reports.dependencies import get_session_record_reader
from app.modules.reports.models import Report
from app.modules.runs.models import SessionStatus, SessionType
from app.shared.security import Role
from tests.modules.reports.test_report_generation import (
    AWAL,
    PembacaPalsu,
    _nilai,
    _rekaman,
)

URL = "/reports/validate"
URL_BUAT = "/reports"


@pytest.fixture
def pasang_pembaca():
    def pasang(*rekaman):
        app.dependency_overrides[get_session_record_reader] = lambda: PembacaPalsu(*rekaman)

    yield pasang
    app.dependency_overrides.pop(get_session_record_reader, None)


@pytest.fixture
def dua(pasang_pembaca):
    a = _rekaman("Produk A", [_nilai(1, "0.8", "0.6"), _nilai(2, "0.6", "0.4")])
    b = _rekaman("Produk B", [_nilai(1, "0.4", "0.4"), _nilai(2, "0.2", "0.2")])
    pasang_pembaca(a, b)
    return a, b


def _isi(rekaman, **ubah) -> dict:
    return {"session_ids": [str(r.facts.session_id) for r in rekaman]} | ubah


def _kode(respons, jenis: str) -> set[str]:
    return {v["code"] for v in respons.json()["violations"] if v["kind"] == jenis}


class TestAnswer:
    def test_a_clean_selection_is_valid_and_says_so(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua, score_scope="test"))

        # Assert
        assert respons.status_code == 200
        assert respons.json() == {
            "valid": True,
            "violations": [],
            "warnings": [],
            "available_scopes": ["test"],
        }

    def test_score_scope_and_measurement_filter_have_defaults(self, as_role, dua):
        # Arrange
        client = as_role(Role.VIEWER)

        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        assert respons.status_code == 200
        assert respons.json()["valid"] is True

    def test_every_violation_is_listed_at_once_with_kind_code_message_and_session(
        self, as_role, pasang_pembaca
    ):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
        batal = _rekaman("Batal", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED)
        lain = _rekaman("Lain", [_nilai(1, "0.5", "0.5")], rubrik=uuid.UUID(int=99))
        pasang_pembaca(a, batal, lain)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi((a, batal, lain)))

        # Assert
        assert respons.status_code == 200
        isi = respons.json()
        assert isi["valid"] is False
        assert {(v["kind"], v["code"]) for v in isi["violations"]} == {
            ("session", "SESSION_CANCELLED"),
            ("requirement", "RUBRIC_VERSION"),
        }
        sesi = next(v for v in isi["violations"] if v["kind"] == "session")
        assert sesi["session_id"] == str(batal.facts.session_id)
        assert all(v["message"] for v in isi["violations"])

    def test_warnings_do_not_block_and_name_their_session(self, as_role, pasang_pembaca):
        # Arrange
        lama = _rekaman(
            "Manual",
            [_nilai(1, "0.5", "0.5")],
            session_type=SessionType.MANUAL,
            dari=AWAL,
            sampai=AWAL + timedelta(days=10),
            repetisi=3,
        )
        otomatis = _rekaman("Otomatis", [_nilai(1, "0.5", "0.5")])
        pasang_pembaca(lama, otomatis)
        client = as_role(Role.VIEWER)

        # Act
        respons = client.post(URL, json=_isi((lama, otomatis)))

        # Assert
        isi = respons.json()
        assert isi["valid"] is True
        assert {(w["code"], w["session_id"]) for w in isi["warnings"]} == {
            ("REPETITIONS_DIFFER", None),
            ("MANUAL_ANSWER_RANGE_OVER_LIMIT", str(lama.facts.session_id)),
        }

    def test_no_common_cases_is_a_violation_without_any_numbers(self, as_role, pasang_pembaca):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
        b = _rekaman("B", [_nilai(2, "0.5", "0.5")])
        pasang_pembaca(a, b)
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi((a, b)))

        # Assert
        assert respons.json()["valid"] is False
        assert _kode(respons, "case_slice") == {"NO_SHARED_CASES"}
        assert respons.json()["available_scopes"] == []

    def test_nothing_is_saved(self, as_role, dua, db_session):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        client.post(URL, json=_isi(dua, score_scope="test"))

        # Assert
        assert db_session.query(Report).count() == 0


class TestScopesByRole:
    @pytest.fixture
    def campuran(self, pasang_pembaca):
        def isi():
            return [_nilai(1, "0.5", "0.5"), _nilai(2, "0.5", "0.5", SplitTag.DEV)]

        a, b = _rekaman("A", isi()), _rekaman("B", isi())
        pasang_pembaca(a, b)
        return a, b

    def test_developer_ai_may_choose_every_scope_on_a_mixed_slice(self, as_role, campuran):
        # Arrange
        client = as_role(Role.DEVELOPER_AI)

        # Act
        respons = client.post(URL, json=_isi(campuran))

        # Assert
        assert respons.json()["available_scopes"] == ["test", "dev", "dev_test"]

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    def test_viewer_and_contributor_only_ever_get_test(self, as_role, campuran, role):
        # Arrange
        client = as_role(role)

        # Act
        respons = client.post(URL, json=_isi(campuran))

        # Assert
        assert respons.json()["available_scopes"] == ["test"]

    @pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
    @pytest.mark.parametrize("scope", ["dev", "dev_test"])
    def test_a_dev_scope_is_403_for_viewer_and_contributor(self, as_role, dua, role, scope):
        # Arrange
        client = as_role(role)

        # Act
        respons = client.post(URL, json=_isi(dua, score_scope=scope))

        # Assert
        assert respons.status_code == 403
        assert respons.json()["code"] == "REPORT_SCOPE_FORBIDDEN"


class TestRefused:
    def test_an_unknown_session_is_404_and_names_the_missing_ids(self, as_role, dua):
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

    def test_without_a_session_the_answer_is_401(self, client, dua):
        # Act
        respons = client.post(URL, json=_isi(dua))

        # Assert
        assert respons.status_code == 401

    @pytest.mark.parametrize(
        "ubah",
        [
            {"session_ids": ["bukan-uuid"]},
            {"session_ids": None},
            {"score_scope": "semua"},
            {"score_scope": None},
            {"measurement_filter": "setengah"},
            {"measurement_filter": None},
            {"tidak_dikenal": 1},
        ],
    )
    def test_a_malformed_request_is_422(self, as_role, dua, ubah):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        respons = client.post(URL, json=_isi(dua) | ubah)

        # Assert
        assert respons.status_code == 422

    def test_an_empty_selection_is_not_an_error(self, as_role):
        # Arrange
        client = as_role(Role.ADMIN)
        app.dependency_overrides[get_session_record_reader] = lambda: PembacaPalsu()

        # Act
        try:
            respons = client.post(URL, json={"session_ids": []})
        finally:
            app.dependency_overrides.pop(get_session_record_reader, None)

        # Assert
        assert respons.status_code == 200
        assert _kode(respons, "selection") == {"NEED_AT_LEAST_TWO_PRODUCTS"}


class TestSameRulesAsCreate:
    def test_post_reports_still_refuses_what_validate_flagged(self, as_role, pasang_pembaca):
        # Arrange
        a = _rekaman("A", [_nilai(1, "0.5", "0.5")])
        lain = _rekaman("Lain", [_nilai(1, "0.5", "0.5")], rubrik=uuid.UUID(int=99))
        pasang_pembaca(a, lain)
        client = as_role(Role.ADMIN)
        isi = _isi((a, lain))

        # Act
        validasi = client.post(URL, json=isi)
        buat = client.post(URL_BUAT, json=isi | {"title": "Laporan", "score_scope": "test"})

        # Assert
        assert buat.status_code == 409
        syarat_buat = [t["requirement"] for t in buat.json()["rejections"]]
        assert sorted(_kode(validasi, "requirement")) == sorted(syarat_buat)

    def test_post_reports_accepts_what_validate_called_valid(self, as_role, dua):
        # Arrange
        client = as_role(Role.ADMIN)
        isi = _isi(dua, score_scope="test")

        # Act
        validasi = client.post(URL, json=isi)
        buat = client.post(URL_BUAT, json=isi | {"title": "Laporan"})

        # Assert
        assert validasi.json()["valid"] is True
        assert buat.status_code == 201
