"""GET /reports/eligible-sessions. PBI-15 SCRUM-79 SCRUM-313.

Pembaca sesi diganti pembaca palsu; peran dipalsukan lewat as_role. Yang diuji: bentuk
response, kode status, dan filter.
"""

import pytest

from app.main import app
from app.modules.reports.dependencies import get_session_record_reader
from app.modules.runs.models import SessionType
from app.shared.security import Role
from tests.modules.reports.test_report_generation import PembacaPalsu, _nilai, _rekaman

URL = "/reports/eligible-sessions"


@pytest.fixture
def pasang_pembaca():
    def pasang(*rekaman):
        app.dependency_overrides[get_session_record_reader] = lambda: PembacaPalsu(*rekaman)

    yield pasang
    app.dependency_overrides.pop(get_session_record_reader, None)


@pytest.fixture
def campuran(pasang_pembaca):
    layak = _rekaman("Produk A", [_nilai(1, "0.5", "0.5")])
    batal = _rekaman("Produk B", [_nilai(1, "0.5", "0.5")], status="cancelled")
    manual = _rekaman(
        "Produk C",
        [_nilai(1, "0.5", "0.5")],
        session_type=SessionType.MANUAL,
        status="finalized",
    )
    pasang_pembaca(layak, batal, manual)
    return layak, batal, manual


class TestList:
    def test_returns_every_session_with_its_eligibility_and_reasons(self, as_role, campuran):
        # Arrange
        layak, batal, _ = campuran
        client = as_role(Role.ADMIN)

        # Act
        respons = client.get(URL)

        # Assert
        assert respons.status_code == 200
        isi = respons.json()
        assert [item["product_name"] for item in isi] == ["Produk A", "Produk B", "Produk C"]
        assert isi[0] == {
            "session_id": str(layak.facts.session_id),
            "product_name": "Produk A",
            "session_type": "automatic",
            "purpose": "official",
            "status": "finished",
            "eligible": True,
            "reasons": [],
        }
        assert isi[1]["session_id"] == str(batal.facts.session_id)
        assert isi[1]["eligible"] is False
        assert [alasan["code"] for alasan in isi[1]["reasons"]] == ["SESSION_CANCELLED"]
        assert isi[1]["reasons"][0]["message"]

    @pytest.mark.parametrize("role", list(Role))
    def test_every_role_may_read_the_list(self, as_role, campuran, role):
        # Act
        respons = as_role(role).get(URL)

        # Assert
        assert respons.status_code == 200
        assert len(respons.json()) == 3

    def test_the_manual_filter_leaves_automatic_sessions_out(self, as_role, campuran):
        # Act
        respons = as_role(Role.ADMIN).get(URL, params={"measurement_filter": "manual"})

        # Assert
        assert respons.status_code == 200
        assert [item["product_name"] for item in respons.json()] == ["Produk C"]

    def test_an_unknown_filter_is_422(self, as_role, campuran):
        # Act
        respons = as_role(Role.ADMIN).get(URL, params={"measurement_filter": "semua"})

        # Assert
        assert respons.status_code == 422

    def test_no_sessions_is_an_empty_list(self, as_role, pasang_pembaca):
        # Arrange
        pasang_pembaca()

        # Act
        respons = as_role(Role.ADMIN).get(URL)

        # Assert
        assert respons.status_code == 200
        assert respons.json() == []

    def test_without_a_session_the_answer_is_401(self, client, campuran):
        # Act
        respons = client.get(URL)

        # Assert
        assert respons.status_code == 401
