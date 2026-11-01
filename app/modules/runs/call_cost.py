"""Biaya satu panggilan. Bukan rumus perkiraan SCRUM-206."""

from decimal import ROUND_HALF_UP, Decimal

from app.shared.exceptions import InternalError

_ONE_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.01")


def actual_cost_idr(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    usd_per_million_input_tokens: Decimal,
    usd_per_million_output_tokens: Decimal,
    usd_idr_rate: Decimal,
) -> Decimal | None:
    """Token kali harga per juta, kali kurs. Keduanya kosong berarti biaya kosong."""
    if input_tokens is None and output_tokens is None:
        return None
    if usd_idr_rate <= 0:
        raise InternalError("Exchange rate must be positive.", code="COST_ESTIMATE_INVALID_INPUT")
    usd = Decimal(0)
    if input_tokens is not None:
        usd += Decimal(input_tokens) / _ONE_MILLION * usd_per_million_input_tokens
    if output_tokens is not None:
        usd += Decimal(output_tokens) / _ONE_MILLION * usd_per_million_output_tokens
    return (usd * usd_idr_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
