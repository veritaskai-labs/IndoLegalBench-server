"""Perakitan dependency FastAPI untuk modul runs (PBI-11, SCRUM-206, SCRUM-207)."""

from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.modules.runs.adapters import (
    CaseCountAdapter,
    InMemorySessionQueue,
    PendingAnswerHistory,
    PendingSpend,
    ProductAdapter,
    ProductCopyAdapter,
    SnapshotSourceAdapter,
)
from app.modules.runs.exchange_rate import (
    ConfiguredExchangeRate,
    ExchangeRateProvider,
    JisdorExchangeRate,
)
from app.modules.runs.pricing import ConfigPriceCatalog
from app.modules.runs.service import SessionQueue, StartSessionService, SummaryService
from app.modules.runs.session_ports import SessionQueueStore
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
        spend=PendingSpend(),
        history=PendingAnswerHistory(),
        prices=ConfigPriceCatalog(settings.cost_model_prices),
        rates=rates,
        safety_margin_percent=settings.cost_safety_margin_percent,
        default_avg_answer_tokens=settings.cost_default_avg_answer_tokens,
    )


@lru_cache
def get_session_store() -> SessionQueueStore:
    """Satu antrean untuk seluruh proses. Diganti adapter tabel saat SCRUM-205 masuk."""
    return InMemorySessionQueue()


def get_start_service(
    summary: SummaryService = Depends(get_summary_service),
    sessions: SessionQueueStore = Depends(get_session_store),
) -> StartSessionService:
    return StartSessionService(summary=summary, sessions=sessions)


def build_session_queue(db: Session) -> SessionQueue:
    """Antrean untuk runner worker (QueuePort SCRUM-208), di luar request HTTP.

    Memakai penyimpanan sesi yang sama dengan POST /measurements.
    """
    return SessionQueue(
        sessions=get_session_store(),
        snapshots=SnapshotSourceAdapter(db),
        products=ProductCopyAdapter(db),
    )
