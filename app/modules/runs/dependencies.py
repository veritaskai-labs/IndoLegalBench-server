"""Perakitan dependency FastAPI untuk modul runs (PBI-11, SCRUM-206)."""

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.modules.runs.adapters import (
    CallLogAnswerHistory,
    CallLogSpend,
    CaseCountAdapter,
    ProductAdapter,
)
from app.modules.runs.call_log import CallLogService
from app.modules.runs.exchange_rate import (
    ConfiguredExchangeRate,
    ExchangeRateProvider,
    JisdorExchangeRate,
)
from app.modules.runs.pricing import ConfigPriceCatalog
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.service import SummaryService
from app.modules.runs.worker import run_next
from app.shared.config import Settings, get_settings
from app.shared.database import get_db


@lru_cache
def get_exchange_rate() -> ExchangeRateProvider:
    """Satu objek untuk seluruh proses, supaya kurs harian tidak diambil ulang tiap request."""
    settings = get_settings()
    return JisdorExchangeRate(
        url=settings.jisdor_api_url,
        api_key=settings.jisdor_api_key,
        fallback=ConfiguredExchangeRate(settings.cost_usd_idr_fallback_rate),
    )


def get_summary_service(
    db: Session = Depends(get_db),
    rates: ExchangeRateProvider = Depends(get_exchange_rate),
    settings: Settings = Depends(get_settings),
) -> SummaryService:
    return SummaryService(
        products=ProductAdapter(db),
        cases=CaseCountAdapter(db),
        spend=CallLogSpend(db),
        history=CallLogAnswerHistory(db),
        prices=ConfigPriceCatalog(settings.cost_model_prices),
        rates=rates,
        safety_margin_percent=settings.cost_safety_margin_percent,
        default_avg_answer_tokens=settings.cost_default_avg_answer_tokens,
    )


def get_call_log_service(
    db: Session,
    rates: ExchangeRateProvider,
) -> CallLogService:
    """Satu-satunya tempat produksi yang membuat CallLogService."""
    return CallLogService(
        products=ProductAdapter(db),
        repository=CallLogRepository(db),
        rates=rates,
        clock=lambda: datetime.now(UTC),
    )


def run_queued_measurement(
    db: Session,
    queue,
    store,
    adapter,
    product_id,
    *,
    rates: ExchangeRateProvider | None = None,
    **kwargs,
):
    """Pemanggil run_next. Tanpa ini worker tidak menulis call log."""
    return run_next(
        queue,
        store,
        adapter,
        call_log=get_call_log_service(db, rates or get_exchange_rate()),
        product_id=product_id,
        **kwargs,
    )
