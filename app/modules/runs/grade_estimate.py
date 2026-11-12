"""Perkiraan biaya penilaian sebelum tombol Nilai (PBI-14 AC6, SCRUM-286).

Fungsi murni: tidak mengenal HTTP, database, atau jaringan, jadi bisa dipakai ulang oleh
"Coba Nilai Ulang". Biaya tiap panggilan memakai actual_cost_idr, rumus yang sama dengan
JudgeMeter, jadi estimasi dan biaya nyata tidak berbeda cara hitung.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.modules.runs.call_cost import actual_cost_idr
from app.modules.runs.judge import JudgePrices
from app.modules.runs.judge_budget import JudgePricingMissing
from app.shared.exceptions import InternalError

# Satu percobaan memanggil penilai dua kali: kualitas dan format (judge.run_judge).
JUDGE_CALLS_PER_ATTEMPT = 2
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class GradeEstimate:
    attempts_to_grade: int
    judge_calls: int
    input_tokens: int
    output_tokens: int
    cost_idr: Decimal


def estimate_grade(
    *,
    attempt_prompt_tokens: Sequence[tuple[int, int]],
    output_tokens_per_call: int,
    prices: JudgePrices,
) -> GradeEstimate:
    """Perkiraan satu penilaian: tiap percobaan dua panggilan, biaya dijumlah per panggilan.

    attempt_prompt_tokens berisi token prompt kualitas dan format tiap percobaan yang akan
    dinilai. Pembulatan per panggilan sama dengan yang dicatat meter. Harga masuk atau
    keluar yang kosong ditolak (JudgePricingMissing). Angka negatif atau kurs tidak positif
    hanya bisa datang dari kode atau konfigurasi yang rusak, jadi hasilnya 500.
    """
    _require_valid_inputs(attempt_prompt_tokens, output_tokens_per_call)
    if prices.usd_per_million_input_tokens is None or prices.usd_per_million_output_tokens is None:
        raise JudgePricingMissing
    cost = Decimal(0)
    for call_tokens in (tokens for attempt in attempt_prompt_tokens for tokens in attempt):
        cost += _call_cost(call_tokens, output_tokens_per_call, prices)
    attempts = len(attempt_prompt_tokens)
    calls = attempts * JUDGE_CALLS_PER_ATTEMPT
    return GradeEstimate(
        attempts_to_grade=attempts,
        judge_calls=calls,
        input_tokens=sum(sum(attempt) for attempt in attempt_prompt_tokens),
        output_tokens=calls * output_tokens_per_call,
        cost_idr=cost.quantize(_CENT),
    )


def _call_cost(input_tokens: int, output_tokens: int, prices: JudgePrices) -> Decimal:
    cost = actual_cost_idr(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd_per_million_input_tokens=prices.usd_per_million_input_tokens,
        usd_per_million_output_tokens=prices.usd_per_million_output_tokens,
        usd_idr_rate=prices.usd_idr_rate,
    )
    return cost if cost is not None else Decimal(0)


def _require_valid_inputs(
    attempt_prompt_tokens: Sequence[tuple[int, int]], output_tokens_per_call: int
) -> None:
    negative = output_tokens_per_call < 0 or any(
        tokens < 0 for attempt in attempt_prompt_tokens for tokens in attempt
    )
    if negative:
        raise InternalError(
            "Cost estimate inputs are out of range.",
            code="COST_ESTIMATE_INVALID_INPUT",
        )
