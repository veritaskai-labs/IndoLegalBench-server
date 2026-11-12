"""Pemakaian dan anggaran bulanan produk penilai (PBI-14 AC6, SCRUM-271)."""

from dataclasses import dataclass
from decimal import Decimal

from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.judge import JudgePrices


class JudgePricingMissing(Exception):
    """Produk penilai tidak punya harga token masuk atau keluar, jadi biaya tidak bisa dicatat."""


@dataclass(frozen=True)
class JudgeUsage:
    """Pemakaian satu panggilan penilai, disimpan di llm_judge_results."""

    input_tokens: int | None
    output_tokens: int | None
    cost_idr: Decimal | None
    usd_idr_rate: Decimal | None
    failure_kind: str | None = None
    model_version: str | None = None

    @classmethod
    def from_row(cls, row: object) -> "JudgeUsage":
        return cls(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cost_idr=row.cost_idr,
            usd_idr_rate=row.usd_idr_rate,
            failure_kind=row.failure_kind,
            model_version=row.model_version,
        )


class JudgeMeter:
    """Harga dan sisa anggaran bulanan produk penilai selama satu penilaian sesi.

    Sisa dihitung dari batas bulanan dikurangi pengeluaran bulan ini yang sudah tercatat,
    lalu dipotong tiap panggilan. Satu percobaan memanggil penilai dua kali (kualitas dan
    format), jadi anggaran dicek sebelum percobaan dan bisa terlampaui sedikit oleh
    percobaan terakhir.
    """

    def __init__(self, prices: JudgePrices, remaining_idr: Decimal) -> None:
        self.prices = prices
        self._remaining_idr = remaining_idr

    def has_budget(self) -> bool:
        return self._remaining_idr > 0

    def charge(self, cost_idr: Decimal | None) -> None:
        if cost_idr is not None:
            self._remaining_idr -= cost_idr


def open_judge_meter(
    *, binding: object, spent_idr: Decimal, rates: ExchangeRateProvider
) -> JudgeMeter:
    """Meter dari harga dan batas bulanan milik produk penilai (judge_service.JudgeBinding).

    Harga token masuk dan keluar wajib ada: tanpa itu biaya panggilan tidak bisa dihitung
    setelah panggilan berbayar selesai, jadi penolakannya di sini, sebelum penilai dipanggil.
    """
    if binding.price_input_usd_per_mtok is None or binding.price_output_usd_per_mtok is None:
        raise JudgePricingMissing
    prices = JudgePrices(
        usd_per_million_input_tokens=binding.price_input_usd_per_mtok,
        usd_per_million_output_tokens=binding.price_output_usd_per_mtok,
        usd_idr_rate=rates.usd_idr(),
    )
    return JudgeMeter(prices, binding.monthly_budget_idr - spent_idr)
