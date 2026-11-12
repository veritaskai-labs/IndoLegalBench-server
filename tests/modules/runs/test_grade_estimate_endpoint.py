"""Endpoint perkiraan biaya penilaian. PBI-14 AC6, SCRUM-78 SCRUM-286.

POST /measurements/{id}/grade/estimate: Admin melihat jumlah percobaan, panggilan judge, token,
biaya rupiah, dan sisa anggaran sebelum menekan Nilai. Penyedia kurs dan penghitung token diganti
tiruan lewat dependency override. Judge tidak dipanggil dan tidak ada yang disimpan.

Label: Positif = angka sesuai rumus, Negatif = ditolak dengan kode jelas,
Edge = tanpa body, anggaran tidak cukup, nilai konfigurasi di luar batas.
"""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError as SettingsError
from sqlalchemy import func, select

from app.main import app
from app.modules.grading.models import GradingRun
from app.modules.runs.dependencies import get_exchange_rate, get_token_counter
from app.modules.runs.exchange_rate import ConfiguredExchangeRate
from app.shared.config import Settings, get_settings
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_grade import (  # noqa: F401  (fixture)
    _setup_sesi_lengkap,
    pembuat,
    produk,
)

_TOKEN_KELUAR = 100
# (10 * 1 + 100 * 2) / 1e6 * 16000 = 3.36 per panggilan, empat panggilan.
_BIAYA_SESI = Decimal("13.44")


class _Counter:
    def count(self, text: str, provider_type: str) -> int:
        return 10


@pytest.fixture
def penilai(db_session):
    item = _otomatis(
        "Penilai Endpoint Estimasi",
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
        monthly_budget_idr=Decimal("1000"),
    )
    db_session.add(item)
    db_session.commit()
    return item


@pytest.fixture
def sesi(db_session, produk, pembuat, penilai, monkeypatch):  # noqa: F811  (fixture)
    hasil = _setup_sesi_lengkap(db_session, produk, pembuat)
    hasil.judge_provider_id = penilai.id
    db_session.commit()
    pengaturan = get_settings().model_copy(update={"judge_estimate_output_tokens": _TOKEN_KELUAR})
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: pengaturan)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_exchange_rate,
        lambda: ConfiguredExchangeRate(Decimal("16000")),
    )
    monkeypatch.setitem(app.dependency_overrides, get_token_counter, lambda: _Counter())
    return hasil


def _url(sesi_id) -> str:
    return f"/measurements/{sesi_id}/grade/estimate"


def test_admin_mendapat_perkiraan_biaya_lengkap(as_role, sesi, penilai):
    """Positif: percobaan, panggilan, token, biaya, kurs, sisa anggaran, dan kecukupannya."""
    # Arrange
    admin = as_role(Role.ADMIN)

    # Act
    respons = admin.post(_url(sesi.id), json={})

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.json() == {
        "judge_provider_id": str(penilai.id),
        "rubric_version": "v1",
        "attempts_to_grade": 2,
        "judge_calls": 4,
        "estimated_input_tokens": 40,
        "estimated_output_tokens": 400,
        "estimated_cost_idr": str(_BIAYA_SESI),
        "usd_idr_rate": "16000",
        "remaining_budget_idr": "1000.00",
        "budget_sufficient": True,
    }


def test_tanpa_body_memakai_judge_sesi_dan_rubrik_aktif(as_role, sesi, penilai):
    """Edge: body boleh kosong sama seperti tombol Nilai."""
    # Act
    respons = as_role(Role.ADMIN).post(_url(sesi.id))

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.json()["judge_provider_id"] == str(penilai.id)
    assert respons.json()["rubric_version"] == "v1"


def test_anggaran_tidak_cukup_tetap_200_dengan_penanda(as_role, db_session, sesi, penilai):
    """Edge: tabel perkiraan tetap tampil, FE memutuskan dari budget_sufficient."""
    # Arrange
    penilai.monthly_budget_idr = Decimal("10")
    db_session.commit()

    # Act
    respons = as_role(Role.ADMIN).post(_url(sesi.id), json={})

    # Assert
    assert respons.status_code == 200, respons.text
    assert respons.json()["budget_sufficient"] is False
    assert respons.json()["remaining_budget_idr"] == "10.00"


def test_judge_tanpa_harga_lengkap_dijawab_422_dengan_kode(as_role, db_session, sesi, penilai):
    """Negatif: kode JUDGE_PRICING_MISSING yang jelas, bukan 500."""
    # Arrange
    penilai.price_output_usd_per_mtok = None
    db_session.commit()

    # Act
    respons = as_role(Role.ADMIN).post(_url(sesi.id), json={})

    # Assert
    assert respons.status_code == 422
    assert respons.json()["code"] == "JUDGE_PRICING_MISSING"


@pytest.mark.parametrize("peran", [Role.VIEWER, Role.DEVELOPER_AI, Role.CONTRIBUTOR])
def test_selain_admin_ditolak_403(as_role, sesi, peran):
    """Negatif: perkiraan biaya hanya untuk Admin."""
    # Act
    respons = as_role(peran).post(_url(sesi.id), json={})

    # Assert
    assert respons.status_code == 403


def test_tanpa_login_ditolak_401(client, sesi):
    """Negatif: permintaan tanpa sesi login tidak dilayani."""
    # Act
    respons = client.post(_url(sesi.id), json={})

    # Assert
    assert respons.status_code == 401


def test_sesi_tidak_ada_dijawab_404(as_role, sesi):
    """Negatif: id sesi yang tidak ada."""
    # Act
    respons = as_role(Role.ADMIN).post(_url(uuid.uuid4()), json={})

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "MEASUREMENT_NOT_FOUND"


def test_id_judge_bukan_uuid_ditolak_422(as_role, sesi):
    """Negatif: bentuk body divalidasi schema, sebelum service dipanggil."""
    # Act
    respons = as_role(Role.ADMIN).post(_url(sesi.id), json={"judge_provider_id": "bukan-uuid"})

    # Assert
    assert respons.status_code == 422


def test_perkiraan_tidak_menyimpan_run_penilaian(as_role, db_session, sesi):
    """Positif: tanpa efek samping, jadi aman dipanggil berulang kali."""
    # Arrange
    admin = as_role(Role.ADMIN)

    # Act
    admin.post(_url(sesi.id), json={})
    admin.post(_url(sesi.id), json={})

    # Assert
    assert db_session.scalar(select(func.count()).select_from(GradingRun)) == 0


def test_token_keluar_judge_punya_nilai_bawaan_positif():
    """Positif: endpoint bisa jalan tanpa env tambahan."""
    # Act
    pengaturan = Settings()

    # Assert
    assert pengaturan.judge_estimate_output_tokens == 300


@pytest.mark.parametrize("nilai", [0, -5])
def test_token_keluar_judge_nol_atau_negatif_ditolak_saat_konfigurasi_dibaca(nilai):
    """Negatif: konfigurasi rusak gagal di awal, bukan menghasilkan estimasi nol."""
    # Act
    with pytest.raises(SettingsError):
        Settings(judge_estimate_output_tokens=nilai)
