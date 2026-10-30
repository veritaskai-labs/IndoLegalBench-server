"""Katalog harga token untuk perkiraan biaya (PBI-11 AC5, SCRUM-206)."""

import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol

from app.shared.exceptions import InternalError

logger = logging.getLogger(__name__)


class PriceCatalog(Protocol):
    """Sumber harga token. Service bergantung pada ini, bukan pada konfigurasi."""

    def usd_per_million_output_tokens(self, provider_type: str, model_name: str) -> Decimal:
        """Harga USD per 1 juta token output untuk satu model."""
        raise NotImplementedError


class ConfigPriceCatalog:
    """Harga dari konfigurasi, berkunci "provider_type:model_name".

    Model tanpa harga adalah kesalahan konfigurasi server (500 dan log), bukan
    angka tebakan. Harga nol sah untuk model gratis.
    """

    def __init__(self, prices: Mapping[str, Decimal]) -> None:
        self._prices = dict(prices)

    def usd_per_million_output_tokens(self, provider_type: str, model_name: str) -> Decimal:
        price = self._prices.get(f"{provider_type}:{model_name}")
        if price is None:
            logger.error("No price configured for %s model %s", provider_type, model_name)
            raise InternalError(
                f"No price is configured for {provider_type} model {model_name}.",
                code="PRICE_NOT_CONFIGURED",
            )
        return price
