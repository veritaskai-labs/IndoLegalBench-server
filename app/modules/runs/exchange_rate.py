"""Kurs USD ke rupiah untuk perkiraan biaya (PBI-11 AC5, SCRUM-206)."""

import logging
from decimal import Decimal
from typing import Protocol

from app.shared.exceptions import InternalError

logger = logging.getLogger(__name__)


class ExchangeRateProvider(Protocol):
    """Sumber kurs. Service bergantung pada ini, bukan pada layanan tertentu."""

    def usd_idr(self) -> Decimal:
        """Rupiah per satu USD."""
        raise NotImplementedError


class ConfiguredExchangeRate:
    """Kurs tetap dari konfigurasi, cadangan sebelum kurs harian otomatis ada.

    Kurs kosong atau tidak positif adalah kesalahan konfigurasi server (500 dan
    log), bukan angka tebakan.
    """

    def __init__(self, rate: Decimal | None) -> None:
        self._rate = rate

    def usd_idr(self) -> Decimal:
        if self._rate is None or self._rate <= 0:
            logger.error("USD to IDR exchange rate is not configured")
            raise InternalError(
                "The USD to IDR exchange rate is not configured.",
                code="EXCHANGE_RATE_NOT_CONFIGURED",
            )
        return self._rate
