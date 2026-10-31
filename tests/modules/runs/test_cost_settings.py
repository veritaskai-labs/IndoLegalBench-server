"""Cost settings read from the environment. SCRUM-76 SCRUM-206.

Margin, model prices and the fallback exchange rate are configuration
(PBI-11 AC5). Prices and the rate have no default, so nothing is guessed.
A bad value stops the app at startup instead of failing on a request.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.shared.config import Settings

KUNCI_UJI = "kunci-uji"  # pragma: allowlist secret


def _settings(monkeypatch, **env) -> Settings:
    for nama, nilai in env.items():
        monkeypatch.setenv(nama, nilai)
    return Settings(_env_file=None)


def test_bawaan_margin_20_persen_tanpa_harga_dan_tanpa_kurs(monkeypatch):
    # Arrange + Act
    settings = _settings(monkeypatch)

    # Assert
    assert settings.cost_safety_margin_percent == Decimal("20")
    assert settings.cost_model_prices == {}
    assert settings.cost_usd_idr_fallback_rate is None


def test_margin_dan_kurs_dibaca_dari_environment_sebagai_decimal(monkeypatch):
    # Arrange + Act
    settings = _settings(
        monkeypatch,
        COST_SAFETY_MARGIN_PERCENT="25",
        COST_USD_IDR_FALLBACK_RATE="16250.5",
    )

    # Assert
    assert settings.cost_safety_margin_percent == Decimal("25")
    assert settings.cost_usd_idr_fallback_rate == Decimal("16250.5")


def test_harga_model_dibaca_dari_json_environment(monkeypatch):
    # Arrange + Act
    settings = _settings(
        monkeypatch,
        COST_MODEL_PRICES='{"openai_compatible:model-a": "0.42", "gemini_interactions:model-b": "2.5"}',
    )

    # Assert
    assert settings.cost_model_prices == {
        "openai_compatible:model-a": Decimal("0.42"),
        "gemini_interactions:model-b": Decimal("2.5"),
    }


def test_rata_rata_token_bawaan_tanpa_nilai_dan_dibaca_dari_environment(monkeypatch):
    # Arrange + Act
    kosong = _settings(monkeypatch)
    terisi = _settings(monkeypatch, COST_DEFAULT_AVG_ANSWER_TOKENS="750.5")

    # Assert: aturan saat belum ada riwayat masih TBA, jadi tidak ada angka bawaan
    assert kosong.cost_default_avg_answer_tokens is None
    assert terisi.cost_default_avg_answer_tokens == Decimal("750.5")


@pytest.mark.parametrize("nilai", ["0", "-10"])
def test_rata_rata_token_bawaan_nol_atau_negatif_menghentikan_aplikasi(monkeypatch, nilai):
    # Arrange
    monkeypatch.setenv("COST_DEFAULT_AVG_ANSWER_TOKENS", nilai)

    # Act
    with pytest.raises(ValidationError) as galat:
        Settings(_env_file=None)

    # Assert
    assert "cost_default_avg_answer_tokens" in str(galat.value).lower()


def test_jisdor_bawaan_tanpa_key_dan_alamat_dari_dokumentasi_layanan(monkeypatch):
    # Arrange + Act
    settings = _settings(monkeypatch)

    # Assert: key tidak punya nilai bawaan, alamat boleh punya
    assert settings.jisdor_api_key == ""
    assert settings.jisdor_api_url.endswith("/api/v1/central-bank/bi/latest")
    assert settings.jisdor_api_url.startswith("https://")


def test_jisdor_alamat_dan_key_dibaca_dari_environment(monkeypatch):
    # Arrange + Act
    settings = _settings(
        monkeypatch,
        JISDOR_API_URL="https://rates.example.test/latest",
        JISDOR_API_KEY=KUNCI_UJI,
    )

    # Assert
    assert settings.jisdor_api_url == "https://rates.example.test/latest"
    assert settings.jisdor_api_key == KUNCI_UJI


@pytest.mark.parametrize(
    ("nama", "nilai"),
    [
        ("COST_SAFETY_MARGIN_PERCENT", "-1"),
        ("COST_USD_IDR_FALLBACK_RATE", "0"),
        ("COST_USD_IDR_FALLBACK_RATE", "-16000"),
        ("COST_MODEL_PRICES", '{"openai_compatible:model-a": "-0.1"}'),
    ],
)
def test_nilai_negatif_atau_kurs_nol_menghentikan_aplikasi_saat_start(monkeypatch, nama, nilai):
    # Arrange
    monkeypatch.setenv(nama, nilai)

    # Act
    with pytest.raises(ValidationError) as galat:
        Settings(_env_file=None)

    # Assert
    assert nama.lower() in str(galat.value).lower()
