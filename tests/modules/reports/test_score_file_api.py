"""GET /reports/sessions/{session_id}/csv. PBI-16 SCRUM-83 SCRUM-327.

Sumber data sesi diganti tiruan; penyimpanan memakai adapter SQL sungguhan di SQLite,
jadi tes ini juga membuktikan berkas yang diserahkan tersimpan (AC7).
"""

import uuid

import pytest

from app.main import app
from app.modules.reports.dependencies import get_score_source
from app.modules.reports.models import SessionScoreFile
from app.modules.runs.models import SessionStatus
from app.shared.security import Role
from tests.modules.reports.test_report_generation import _nilai, _rekaman
from tests.modules.reports.test_score_files import SumberTiruan, _data

URL = "/reports/sessions/{}/csv"


@pytest.fixture
def pasang_sumber():
    def pasang(*data):
        app.dependency_overrides[get_score_source] = lambda: SumberTiruan(*data)

    yield pasang
    app.dependency_overrides.pop(get_score_source, None)


@pytest.fixture
def layak(pasang_sumber):
    data = _data()
    pasang_sumber(data)
    return data


@pytest.fixture
def dibatalkan(pasang_sumber):
    data = _data(_rekaman("Produk B", [_nilai(1, "0.5", "0.5")], status=SessionStatus.CANCELLED))
    pasang_sumber(data)
    return data


def _url(data) -> str:
    return URL.format(data.record.facts.session_id)


class TestDownload:
    def test_sesi_layak_diunduh_sebagai_berkas_csv(self, as_role, layak):
        # Act
        respons = as_role(Role.VIEWER).get(_url(layak))

        # Assert
        sesi_id = layak.record.facts.session_id
        assert respons.status_code == 200
        assert respons.headers["content-type"] == "text/csv; charset=utf-8"
        assert respons.headers["content-disposition"] == (
            f'attachment; filename="skor-{sesi_id}.csv"'
        )
        teks = respons.content.decode("utf-8-sig")
        assert teks.splitlines()[0].startswith("Kasus,Versi Kasus,Produk")
        assert "0.80" in teks

    @pytest.mark.parametrize("role", list(Role))
    def test_semua_peran_yang_login_boleh_mengunduh(self, as_role, layak, role):
        # Act
        respons = as_role(role).get(_url(layak))

        # Assert
        assert respons.status_code == 200

    def test_berkas_yang_diunduh_tersimpan_sekali(self, as_role, layak, db_session):
        # Arrange
        client = as_role(Role.ADMIN)

        # Act
        pertama = client.get(_url(layak))
        kedua = client.get(_url(layak))

        # Assert
        assert pertama.content == kedua.content
        tersimpan = db_session.query(SessionScoreFile).one()
        assert tersimpan.session_id == layak.record.facts.session_id
        assert tersimpan.content == pertama.content


class TestRejection:
    def test_sesi_tidak_layak_ditolak_409_dengan_alasannya(self, as_role, dibatalkan, db_session):
        # Act
        respons = as_role(Role.ADMIN).get(_url(dibatalkan))

        # Assert
        assert respons.status_code == 409
        isi = respons.json()
        assert isi["code"] == "SESSION_CANCELLED"
        # Handler DomainError meratakan details ke badan respons, di samping code dan message.
        assert [alasan["code"] for alasan in isi["reasons"]] == ["SESSION_CANCELLED"]
        assert isi["reasons"][0]["message"]
        assert isi["message"]
        assert db_session.query(SessionScoreFile).count() == 0

    def test_sesi_yang_tidak_ada_404(self, as_role, pasang_sumber):
        # Arrange
        pasang_sumber()

        # Act
        respons = as_role(Role.ADMIN).get(URL.format(uuid.uuid4()))

        # Assert
        assert respons.status_code == 404

    def test_tanpa_sesi_login_401(self, client, layak):
        # Act
        respons = client.get(_url(layak))

        # Assert
        assert respons.status_code == 401
