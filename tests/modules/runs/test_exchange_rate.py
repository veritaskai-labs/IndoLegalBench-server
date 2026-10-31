"""USD to IDR rate for the cost estimate. SCRUM-76 SCRUM-206.

PBI-11 AC5 asks for a live daily rate. This slice only has the port and
a configured rate. A missing or non-positive rate is a server
configuration error (500 and a log line), never a guessed number.
"""

import logging
from decimal import Decimal

import pytest

from app.modules.runs import exchange_rate
from app.shared.exceptions import InternalError


def test_kurs_dari_konfigurasi_dikembalikan_apa_adanya():
    # Arrange
    penyedia = exchange_rate.ConfiguredExchangeRate(Decimal("16250.50"))

    # Act
    kurs = penyedia.usd_idr()

    # Assert
    assert kurs == Decimal("16250.50")


@pytest.mark.parametrize("nilai", [None, Decimal("0"), Decimal("-1")])
def test_kurs_kosong_nol_atau_negatif_adalah_kesalahan_konfigurasi_500(nilai):
    # Arrange
    penyedia = exchange_rate.ConfiguredExchangeRate(nilai)

    # Act
    with pytest.raises(InternalError) as galat:
        penyedia.usd_idr()

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "EXCHANGE_RATE_NOT_CONFIGURED"


def test_kurs_tidak_tersedia_dicatat_di_log_error(caplog):
    # Arrange
    penyedia = exchange_rate.ConfiguredExchangeRate(None)

    # Act
    with caplog.at_level(logging.ERROR), pytest.raises(InternalError):
        penyedia.usd_idr()

    # Assert
    assert any("exchange rate" in rekaman.getMessage().lower() for rekaman in caplog.records)
