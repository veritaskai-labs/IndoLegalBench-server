"""Catat satu panggilan. Biaya menyusul di fungsi terpisah."""

import uuid
from datetime import datetime
from decimal import Decimal

from app.modules.runs.call_cost import actual_cost_idr
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.models import CallLog
from app.modules.runs.ports import AttemptRecord
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.summary_ports import ProductPort


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
