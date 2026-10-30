"""Price catalog for the cost estimate. SCRUM-76 SCRUM-206.

PBI-11 AC5: token prices follow the public Gemini and DeepSeek pages and
are kept as configuration. A model with no price is a server
configuration error (500 and a log line), never a guessed number.
"""

import logging
from decimal import Decimal

import pytest

from app.modules.runs import pricing
from app.shared.exceptions import InternalError

PRICES = {
    "openai_compatible:model-a": Decimal("0.42"),
    "gemini_interactions:model-a": Decimal("2.5"),
    "openai_compatible:model-gratis": Decimal("0"),
}


def test_harga_diambil_per_provider_dan_nama_model():
    # Arrange
    katalog = pricing.ConfigPriceCatalog(PRICES)

    # Act
    openai = katalog.usd_per_million_output_tokens("openai_compatible", "model-a")
    gemini = katalog.usd_per_million_output_tokens("gemini_interactions", "model-a")

    # Assert: nama model yang sama di provider berbeda punya harga berbeda
    assert openai == Decimal("0.42")
    assert gemini == Decimal("2.5")


def test_model_gratis_berharga_nol_bukan_belum_dikonfigurasi():
    # Arrange
    katalog = pricing.ConfigPriceCatalog(PRICES)

    # Act
    harga = katalog.usd_per_million_output_tokens("openai_compatible", "model-gratis")

    # Assert
    assert harga == Decimal("0")


def test_model_tanpa_harga_adalah_kesalahan_konfigurasi_500():
    # Arrange
    katalog = pricing.ConfigPriceCatalog(PRICES)

    # Act
    with pytest.raises(InternalError) as galat:
        katalog.usd_per_million_output_tokens("openai_compatible", "model-baru")

    # Assert: pesan menyebut provider dan model supaya Admin tahu apa yang kurang
    assert galat.value.status_code == 500
    assert galat.value.code == "PRICE_NOT_CONFIGURED"
    assert "openai_compatible" in galat.value.message
    assert "model-baru" in galat.value.message


def test_model_tanpa_harga_dicatat_di_log_error(caplog):
    # Arrange
    katalog = pricing.ConfigPriceCatalog(PRICES)

    # Act
    with caplog.at_level(logging.ERROR), pytest.raises(InternalError):
        katalog.usd_per_million_output_tokens("openai_compatible", "model-baru")

    # Assert
    assert any("model-baru" in rekaman.getMessage() for rekaman in caplog.records)


def test_nama_model_dibedakan_huruf_besar_kecil():
    # Arrange
    katalog = pricing.ConfigPriceCatalog(PRICES)

    # Act
    with pytest.raises(InternalError) as galat:
        katalog.usd_per_million_output_tokens("openai_compatible", "MODEL-A")

    # Assert
    assert galat.value.code == "PRICE_NOT_CONFIGURED"


def test_katalog_kosong_menolak_semua_model():
    # Arrange
    katalog = pricing.ConfigPriceCatalog({})

    # Act
    with pytest.raises(InternalError) as galat:
        katalog.usd_per_million_output_tokens("openai_compatible", "model-a")

    # Assert
    assert galat.value.code == "PRICE_NOT_CONFIGURED"


def test_perubahan_pada_dict_sumber_tidak_mengubah_katalog():
    # Arrange
    sumber = {"openai_compatible:model-a": Decimal("0.42")}
    katalog = pricing.ConfigPriceCatalog(sumber)

    # Act
    sumber["openai_compatible:model-a"] = Decimal("99")

    # Assert
    assert katalog.usd_per_million_output_tokens("openai_compatible", "model-a") == Decimal("0.42")
