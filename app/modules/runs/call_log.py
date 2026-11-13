"""Catat satu panggilan. Biaya menyusul di fungsi terpisah."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.modules.providers.usage import TokenUsage
from app.modules.runs.call_cost import actual_cost_idr, cache_rates_from
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.models import CallLog
from app.modules.runs.ports import AttemptRecord
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.summary_ports import ProductInfo, ProductPort

_JAKARTA = ZoneInfo("Asia/Jakarta")
DEFAULT_MONTHLY_BUDGET_IDR = Decimal("500000.00")


def month_start(now: datetime) -> datetime:
    """Awal bulan WIB, dikembalikan dalam UTC. Reset tanggal 1 pukul 00.00 belum dikonfirmasi klien.

    UTC karena started_at ditulis worker dalam UTC. SQLite membuang zona waktu, jadi
    batas WIB akan dibandingkan sebagai jam dinding dengan stempel UTC.
    """
    local = now.astimezone(_JAKARTA)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


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

    def ensure_billable(self, product_id: uuid.UUID, record: AttemptRecord) -> Decimal:
        """Kurs dan harga dicek sebelum attempt disimpan, supaya tidak ada baris yatim."""
        rate = self._rates.usd_idr()
        product = self._products.get(product_id)
        _call_cost(product, record, rate)
        return rate

    def record(
        self,
        product_id: uuid.UUID,
        record: AttemptRecord,
        started: datetime,
        ended: datetime,
        *,
        usd_idr_rate: Decimal | None = None,
    ) -> CallLog:
        rate = self._rates.usd_idr() if usd_idr_rate is None else usd_idr_rate
        label, kind = technical_result(record)
        product = self._products.get(product_id)
        try_no = 1 if record.attempt_id is None else self._repository.next_try_no(record.attempt_id)
        row = CallLog(
            attempt_id=record.attempt_id or uuid.uuid4(),
            product_id=product_id,
            started_at=started,
            ended_at=ended,
            duration_ms=max(int((ended - started).total_seconds() * 1000), 0),
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            uncached_input_tokens=record.uncached_input_tokens,
            cache_read_tokens=record.cache_read_tokens,
            cache_write_tokens=record.cache_write_tokens,
            cache_write_long_tokens=record.cache_write_long_tokens,
            cost_idr=_call_cost(product, record, rate),
            usd_idr_rate=rate,
            model_version=record.model_version,
            result=label,
            failure_kind=kind,
            raw=record.raw,
            try_no=try_no,
            # D4: tidak ada coba ulang otomatis, jadi upaya ke-2 dan seterusnya selalu
            # datang dari tombol pengguna (Coba ulang SCRUM-218, Lanjutkan SCRUM-217).
            is_manual_retry=try_no > 1,
        )
        self._repository.add(row)
        return row

    def failure_rate(self, product_id: uuid.UUID) -> Decimal:
        total, failed = self._repository.attempt_counts(product_id)
        if total == 0:
            return Decimal(0)
        return Decimal(failed) / Decimal(total)


def technical_result(record: AttemptRecord) -> tuple[str, str | None]:
    """Jenis kegagalan teknis saja. Teks jawaban salah bukan kegagalan teknis."""
    kind = record.failure_kind
    if kind:
        return kind, kind
    return "ok", None


def _call_cost(product, record: AttemptRecord, rate: Decimal) -> Decimal | None:
    return actual_cost_idr(
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        usd_per_million_input_tokens=_side_price(product, "price_input_usd_per_mtok"),
        usd_per_million_output_tokens=_side_price(product, "price_output_usd_per_mtok"),
        usd_idr_rate=rate,
        usage=_usage_of(record),
        cache_rates=cache_rates_from(product),
    )


def _usage_of(record: AttemptRecord) -> TokenUsage | None:
    parts = (
        record.uncached_input_tokens,
        record.cache_read_tokens,
        record.cache_write_tokens,
        record.cache_write_long_tokens,
    )
    if all(part is None for part in parts):
        return None
    return TokenUsage(
        uncached_input=record.uncached_input_tokens,
        cache_read=record.cache_read_tokens,
        cache_write=record.cache_write_tokens,
        cache_write_long=record.cache_write_long_tokens,
        output=record.output_tokens,
    )


def _side_price(product, field_name: str) -> Decimal | None:
    if product is None:
        return None
    value = getattr(product, field_name, None)
    if not isinstance(value, Decimal):
        return None
    return value
