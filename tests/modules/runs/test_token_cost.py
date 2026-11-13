"""Biaya dari usage yang sudah di-stub. Tidak ada panggilan penyedia.

Bagian cache yang punya token tetapi belum punya tarif sendiri memakai tarif
input biasa. Pilihan itu masih terbuka dan hanya hidup di satu fungsi. Angka 0
tidak butuh tarif.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Edge: batas atau kasus pinggir.
"""

from decimal import Decimal

from app.modules.providers.usage import TokenUsage
from app.modules.runs.call_cost import actual_cost_idr

_INPUT = Decimal("1")
_OUTPUT = Decimal("3")
_RATE = Decimal("10000")


def _cost(usage: TokenUsage, **rates: Decimal | None) -> Decimal | None:
    return actual_cost_idr(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        usd_per_million_input_tokens=_INPUT,
        usd_per_million_output_tokens=_OUTPUT,
        usd_idr_rate=_RATE,
        usage=usage,
        usd_per_million_cache_read_tokens=rates.get("read"),
        usd_per_million_cache_write_tokens=rates.get("write"),
        usd_per_million_cache_write_long_tokens=rates.get("long"),
    )


def test_split_usage_is_priced_per_bucket():
    """Positif: cache tidak ikut tarif input bila tarifnya sendiri terisi."""
    usage = TokenUsage(
        uncached_input=8,
        cache_read=5,
        cache_write=20,
        cache_write_long=10,
        output=6,
    )

    assert _cost(usage, read=Decimal("0.1"), write=Decimal("1.25"), long=Decimal("2")) == Decimal(
        "0.72"
    )


def test_deepseek_stub_prices_miss_at_the_input_rate():
    """Positif: hit 0 tidak butuh tarif cache. Miss adalah input. Penalaran tidak ditambah."""
    usage = TokenUsage(uncached_input=37, cache_read=0, output=13)

    biaya = actual_cost_idr(
        input_tokens=37,
        output_tokens=13,
        usd_per_million_input_tokens=Decimal("0.15"),
        usd_per_million_output_tokens=Decimal("0.60"),
        usd_idr_rate=Decimal("16000"),
        usage=usage,
    )

    assert biaya == Decimal("0.21")


def test_cache_without_its_own_rate_uses_the_input_rate():
    """Edge: tarif cache kosong memakai tarif input, supaya total rupiah tetap ada."""
    usage = TokenUsage(uncached_input=0, cache_read=1_000_000, output=0)

    assert _cost(usage, read=None) == Decimal("10000.00")


def test_headline_input_is_not_charged_again():
    """Positif: input_tokens judul tidak ditagih di samping bagian cache."""
    usage = TokenUsage(uncached_input=15, cache_read=80, cache_write=5, output=9)

    assert _cost(usage, read=Decimal("0.1"), write=Decimal("1.25")) == Decimal("0.56")
