"""Perkiraan biaya satu pengukuran (PBI-11 AC5, SCRUM-206).

Fungsi murni: tidak mengenal HTTP, database, atau jaringan.
"""

from decimal import ROUND_CEILING, Decimal

from app.shared.exceptions import InternalError

_ONE_MILLION = Decimal(1_000_000)
_ONE_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")


def estimate_cost_idr(
    *,
    total_calls: int,
    avg_answer_tokens: Decimal,
    usd_per_million_output_tokens: Decimal,
    usd_idr_rate: Decimal,
    safety_margin_percent: Decimal,
) -> Decimal:
    """Perkiraan biaya dalam rupiah, dibulatkan ke atas ke dua desimal.

    Panggilan kali rata-rata token jawaban, kali harga per juta token, kali
    kurs, lalu ditambah margin pengaman. Dibulatkan ke atas supaya perkiraan
    tidak pernah lebih kecil dari hitungan sebenarnya. Angka yang tidak masuk
    akal hanya bisa datang dari konfigurasi server yang rusak, jadi hasilnya
    500, bukan 422.
    """
    _require_valid_inputs(
        total_calls=total_calls,
        avg_answer_tokens=avg_answer_tokens,
        usd_per_million_output_tokens=usd_per_million_output_tokens,
        usd_idr_rate=usd_idr_rate,
        safety_margin_percent=safety_margin_percent,
    )
    tokens = Decimal(total_calls) * avg_answer_tokens
    usd = tokens / _ONE_MILLION * usd_per_million_output_tokens
    idr = usd * usd_idr_rate
    with_margin = idr * (1 + safety_margin_percent / _ONE_HUNDRED)
    return with_margin.quantize(_CENT, rounding=ROUND_CEILING)


def _require_valid_inputs(
    *,
    total_calls: int,
    avg_answer_tokens: Decimal,
    usd_per_million_output_tokens: Decimal,
    usd_idr_rate: Decimal,
    safety_margin_percent: Decimal,
) -> None:
    """Kurs harus lebih dari nol. Angka lain tidak boleh negatif."""
    salah = (
        usd_idr_rate <= 0
        or total_calls < 0
        or avg_answer_tokens < 0
        or usd_per_million_output_tokens < 0
        or safety_margin_percent < 0
    )
    if salah:
        raise InternalError(
            "Cost estimate inputs are out of range.",
            code="COST_ESTIMATE_INVALID_INPUT",
        )
