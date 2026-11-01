"""Catat satu panggilan. Biaya menyusul di fungsi terpisah."""

import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.modules.runs.call_cost import actual_cost_idr
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.models import CallLog
from app.modules.runs.ports import AttemptRecord
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.summary_ports import ProductInfo, ProductPort

_JAKARTA = ZoneInfo("Asia/Jakarta")
DEFAULT_MONTHLY_BUDGET_IDR = Decimal("500000.00")


def month_start(now: datetime) -> datetime:
    """Awal bulan WIB. Reset tanggal 1 pukul 00.00 belum dikonfirmasi klien."""
    local = now.astimezone(_JAKARTA)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def monthly_limit(product: ProductInfo) -> Decimal:
    if product.monthly_budget_idr is not None:
        return product.monthly_budget_idr
    if product.provider_type == "gemini_interactions":
        return DEFAULT_MONTHLY_BUDGET_IDR
    name = (product.model_name or "").lower()
    if name.startswith("deepseek"):
        return DEFAULT_MONTHLY_BUDGET_IDR
    return Decimal("0.00")


class CallLogService:
    def __init__(
        self,
        *,
        products: ProductPort,
        repository: CallLogRepository,
        rates: ExchangeRateProvider,
        clock,
    ) -> None:
        self._products = products
        self._repository = repository
        self._rates = rates
        self._clock = clock

    def allows(self, product_id: uuid.UUID) -> bool:
        product = self._products.get(product_id)
        if product is None:
            return False
        spent = self._repository.sum_cost_since(product_id, month_start(self._clock()))
        return spent < monthly_limit(product)

    def record(
        self,
        product_id: uuid.UUID,
        record: AttemptRecord,
        started: datetime,
        ended: datetime,
    ) -> CallLog:
        rate = self._rates.usd_idr()
        label, kind = technical_result(record)
        product = self._products.get(product_id)
        row = CallLog(
            attempt_id=uuid.uuid4(),
            product_id=product_id,
            started_at=started,
            ended_at=ended,
            duration_ms=max(int((ended - started).total_seconds() * 1000), 0),
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_idr=actual_cost_idr(
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                usd_per_million_input_tokens=_side_price(product, "price_input_usd_per_mtok"),
                usd_per_million_output_tokens=_side_price(product, "price_output_usd_per_mtok"),
                usd_idr_rate=rate,
            ),
            usd_idr_rate=rate,
            model_version=record.model_version,
            result=label,
            failure_kind=kind,
            raw=record.raw,
        )
        self._repository.add(row)
        return row


def technical_result(record: AttemptRecord) -> tuple[str, str | None]:
    """Jenis kegagalan teknis saja. Teks jawaban salah bukan kegagalan teknis."""
    kind = record.failure_kind
    if kind:
        return kind, kind
    return "ok", None


def _side_price(product, field_name: str) -> Decimal:
    if product is None:
        return Decimal(0)
    value = getattr(product, field_name)
    if value is None:
        return Decimal(0)
    return value
