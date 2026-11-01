"""Catat satu panggilan. Biaya menyusul di fungsi terpisah."""

import uuid
from datetime import datetime

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
        kind = record.failure_kind
        row = CallLog(
            attempt_id=uuid.uuid4(),
            product_id=product_id,
            started_at=started,
            ended_at=ended,
            duration_ms=max(int((ended - started).total_seconds() * 1000), 0),
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_idr=None,
            usd_idr_rate=self._rates.usd_idr(),
            model_version=record.model_version,
            result=kind or "ok",
            failure_kind=kind,
            raw=record.raw,
        )
        self._repository.add(row)
        return row
