"""Biaya satu panggilan. Bukan rumus perkiraan SCRUM-206."""

import logging
from decimal import ROUND_HALF_UP, Decimal

from app.shared.exceptions import InternalError

logger = logging.getLogger(__name__)

_ONE_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.01")


def _priced_tokens(tokens: int | None, price: Decimal | None, side: str) -> Decimal | None:
    if tokens is None:
        return None
    if price is None:
        logger.error("No %s price configured for a measured call", side)
        raise InternalError(
            f"No {side} price is configured.",
            code="PRICE_NOT_CONFIGURED",
        )
    return Decimal(tokens) / _ONE_MILLION * price


def actual_cost_idr(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    usd_per_million_input_tokens: Decimal | None,
    usd_per_million_output_tokens: Decimal | None,
    usd_idr_rate: Decimal,
) -> Decimal | None:
    """Token kali harga per juta, kali kurs. Keduanya kosong berarti biaya kosong."""
    if input_tokens is None and output_tokens is None:
        return None
    parts = [
        part
        for part in (
            _priced_tokens(input_tokens, usd_per_million_input_tokens, "input"),
            _priced_tokens(output_tokens, usd_per_million_output_tokens, "output"),
        )
        if part is not None
    ]
    if usd_idr_rate <= 0:
        raise InternalError("Exchange rate must be positive.", code="COST_ESTIMATE_INVALID_INPUT")
    usd = sum(parts, Decimal(0))
    return (usd * usd_idr_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
