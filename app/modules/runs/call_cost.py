"""Biaya satu panggilan. Bukan rumus perkiraan SCRUM-206."""

import logging
from decimal import ROUND_HALF_UP, Decimal

from app.modules.providers.usage import TokenUsage
from app.shared.exceptions import InternalError

logger = logging.getLogger(__name__)

_ONE_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.01")
_UNPRICED = object()


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


def _unrated_bucket_rate(ordinary: Decimal | None) -> Decimal | None:
    """Harga bagian cache yang belum punya tarif sendiri.

    Aturan ini masih terbuka (rencana biaya nyata, "Open point"). Sekarang
    bagian itu memakai tarif input biasa supaya baris Laporan tetap punya
    angka rupiah. Mengembalikan None di sini membuat biaya panggilan kosong
    bila Dra memilih aturan itu.
    """
    return ordinary


def actual_cost_idr(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    usd_per_million_input_tokens: Decimal | None,
    usd_per_million_output_tokens: Decimal | None,
    usd_idr_rate: Decimal,
    usage: TokenUsage | None = None,
    usd_per_million_cache_read_tokens: Decimal | None = None,
    usd_per_million_cache_write_tokens: Decimal | None = None,
    usd_per_million_cache_write_long_tokens: Decimal | None = None,
) -> Decimal | None:
    """Token kali harga per juta, kali kurs. Keduanya kosong berarti biaya kosong."""
    if usage is not None and _usage_reported(usage):
        return _usage_cost_idr(
            usage,
            usd_per_million_input_tokens=usd_per_million_input_tokens,
            usd_per_million_output_tokens=usd_per_million_output_tokens,
            usd_per_million_cache_read_tokens=usd_per_million_cache_read_tokens,
            usd_per_million_cache_write_tokens=usd_per_million_cache_write_tokens,
            usd_per_million_cache_write_long_tokens=usd_per_million_cache_write_long_tokens,
            usd_idr_rate=usd_idr_rate,
        )
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
    return _rupiah(parts, usd_idr_rate)


def _usage_reported(usage: TokenUsage) -> bool:
    return any(
        part is not None
        for part in (
            usage.uncached_input,
            usage.cache_read,
            usage.cache_write,
            usage.cache_write_long,
            usage.output,
        )
    )


def _usage_cost_idr(
    usage: TokenUsage,
    *,
    usd_per_million_input_tokens: Decimal | None,
    usd_per_million_output_tokens: Decimal | None,
    usd_per_million_cache_read_tokens: Decimal | None,
    usd_per_million_cache_write_tokens: Decimal | None,
    usd_per_million_cache_write_long_tokens: Decimal | None,
    usd_idr_rate: Decimal,
) -> Decimal | None:
    parts = [
        _priced_tokens(usage.uncached_input, usd_per_million_input_tokens, "input"),
        _cache_part(
            usage.cache_read, usd_per_million_cache_read_tokens, usd_per_million_input_tokens
        ),
        _cache_part(
            usage.cache_write, usd_per_million_cache_write_tokens, usd_per_million_input_tokens
        ),
        _cache_part(
            usage.cache_write_long,
            usd_per_million_cache_write_long_tokens,
            usd_per_million_input_tokens,
        ),
        _priced_tokens(usage.output, usd_per_million_output_tokens, "output"),
    ]
    if _UNPRICED in parts:
        return None
    priced = [part for part in parts if part is not None]
    if not priced:
        return None
    return _rupiah(priced, usd_idr_rate)


def _cache_part(
    tokens: int | None, dedicated: Decimal | None, ordinary: Decimal | None
) -> Decimal | object | None:
    if tokens is None:
        return None
    if tokens == 0:
        return Decimal(0)
    price = dedicated if dedicated is not None else _unrated_bucket_rate(ordinary)
    if price is None:
        return _UNPRICED
    return _priced_tokens(tokens, price, "cache")


def _rupiah(parts: list[Decimal], usd_idr_rate: Decimal) -> Decimal:
    if usd_idr_rate <= 0:
        raise InternalError("Exchange rate must be positive.", code="COST_ESTIMATE_INVALID_INPUT")
    usd = sum(parts, Decimal(0))
    return (usd * usd_idr_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
