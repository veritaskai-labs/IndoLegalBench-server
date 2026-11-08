"""Pemakaian dan anggaran bulanan produk penilai (PBI-14 AC6, SCRUM-271)."""

from dataclasses import dataclass
from decimal import Decimal

from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.judge import JudgePrices


@dataclass(frozen=True)
class JudgeUsage:
    input_tokens: int | None
    output_tokens: int | None
    cost_idr: Decimal | None
    usd_idr_rate: Decimal | None

    @classmethod
    def from_row(cls, row: object) -> "JudgeUsage":
        raise NotImplementedError


class JudgeMeter:
    def __init__(self, prices: JudgePrices, remaining_idr: Decimal) -> None:
        raise NotImplementedError

    def has_budget(self) -> bool:
        raise NotImplementedError

    def charge(self, cost_idr: Decimal | None) -> None:
        raise NotImplementedError


def open_judge_meter(
    *, binding: object, spent_idr: Decimal, rates: ExchangeRateProvider
) -> JudgeMeter:
    raise NotImplementedError
