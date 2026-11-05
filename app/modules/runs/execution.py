"""Perakitan satu sesi pengukuran dari tabel. SCRUM-76 SCRUM-249."""

import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select, text

from app.modules.providers import service as providers_service
from app.modules.runs.adapters import ProductAdapter
from app.modules.runs.call_log import CallLogService
from app.modules.runs.dependencies import build_session_queue, get_exchange_rate
from app.modules.runs.execution_store import ExecutionInterrupted, TableSessionStore
from app.modules.runs.models import (
    Attempt,
    AttemptStatus,
    CallLog,
    MeasurementSession,
    SessionProductCopy,
    SessionStatus,
    SessionType,
    StopReason,
)
from app.modules.runs.pricing import ConfigPriceCatalog
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.summary_ports import ProductInfo
from app.modules.runs.worker import run_next
from app.shared.config import get_settings
from app.shared.exceptions import DomainError

_WORKER_LOCK = 76249


@contextmanager
def worker_lease(bind):
    """Satu dispatcher per DB, termasuk resume. Kunci ditahan melewati commit per call."""
    if bind.dialect.name != "postgresql":
        yield True
        return
    engine = getattr(bind, "engine", bind)
    with engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _WORKER_LOCK}
        )
        connection.commit()
        try:
            yield bool(acquired)
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _WORKER_LOCK})
                connection.commit()


class _ClaimedQueue:
    """SessionQueue sudah membuat snapshot; worker tidak boleh claim sesi lain lagi."""

    def __init__(self, session_id):
        self.session_id = session_id

    def take_next(self):
        return self.session_id


class _FrozenProduct:
    def __init__(self, copy, live_products):
        self.live_products = live_products
        settings = get_settings()
        catalog = ConfigPriceCatalog(
            settings.cost_model_prices, input_prices=settings.cost_model_input_prices
        )
        input_price = copy.price_input_usd_per_mtok
        output_price = copy.price_output_usd_per_mtok
        if input_price is None:
            input_price = catalog.usd_per_million_input_tokens(copy.provider_type, copy.model_name)
        if output_price is None:
            output_price = catalog.usd_per_million_output_tokens(
                copy.provider_type, copy.model_name
            )
        self.product = ProductInfo(
            id=copy.product_id,
            name=copy.name,
            measurement_type=copy.measurement_type,
            is_active=True,
            provider_type=copy.provider_type,
            model_name=copy.model_name,
            monthly_budget_idr=copy.monthly_budget_idr,
            price_input_usd_per_mtok=input_price,
            price_output_usd_per_mtok=output_price,
        )

    def get(self, product_id):
        if product_id != self.product.id:
            return None
        live = self.live_products.get(product_id)
        if live is None:
            return None
        # Prices/model inputs are frozen; the operational budget must honor admin changes.
        return replace(self.product, monthly_budget_idr=live.monthly_budget_idr)


def run_once(
    db,
    *,
    adapter_factory=None,
    rates=None,
    sleep=time.sleep,
    clock=time.monotonic,
    rate_limit=True,
    stopping=lambda: False,
) -> bool:
    """Proses satu sesi; False bila idle atau dispatcher lain memegang lease.

    Exception persistence tidak ditelan: attempt running yang durable mencegah
    pemanggilan ulang otomatis. Pemulihan attempt tidak pasti adalah SCRUM-217.
    """
    with worker_lease(db.get_bind()) as acquired:
        if not acquired:
            return False
        return _run_claimed(db, adapter_factory, rates, sleep, clock, rate_limit, stopping)


def _run_claimed(db, adapter_factory, rates, sleep, clock, rate_limit, stopping):
    session_id = db.scalar(
        select(MeasurementSession.id).where(
            MeasurementSession.status == SessionStatus.RUNNING,
            MeasurementSession.session_type == SessionType.AUTOMATIC,
        )
    )
    if session_id is None:
        session_id = build_session_queue(db).take_next()
    if session_id is None:
        return False
    session = db.get(MeasurementSession, session_id)
    copy = db.scalars(
        select(SessionProductCopy).where(SessionProductCopy.session_id == session_id)
    ).one()
    store = TableSessionStore(db, session_id, stopping=stopping)
    states = list(db.scalars(select(Attempt.status).where(Attempt.session_id == session_id)))
    if AttemptStatus.RUNNING in states:
        raise RuntimeError("uncertain attempt requires recovery before dispatch")
    if AttemptStatus.PENDING not in states:
        # Retrying finalization must not require another credential/provider call or new budget.
        store.mark_status(session_id, SessionStatus.FINISHED)
        return True
    try:
        factory = adapter_factory or providers_service.build_measurement_adapter
        adapter = factory(db, copy)
        live = providers_service.get_product(db, copy.product_id)
        # D2 freezes model parameters/prices; rate limit is an operational limit read at dispatch.
        limit = live.rate_limit_per_minute if rate_limit else None
        exchange = rates or get_exchange_rate()
        exchange.usd_idr()  # Preflight before any paid provider dispatch, not after the response.
        billing = CallLogService(
            products=_FrozenProduct(copy, ProductAdapter(db)),
            repository=CallLogRepository(db),
            rates=exchange,
            clock=lambda: datetime.now(UTC),
        )
    except (DomainError, ValueError):
        prior_call = db.scalar(
            select(CallLog.id)
            .join(Attempt, Attempt.id == CallLog.attempt_id)
            .where(Attempt.session_id == session_id)
            .limit(1)
        )
        if prior_call is None and all(state == AttemptStatus.PENDING for state in states):
            store.mark_status(session_id, SessionStatus.FAILED, stop_reason=StopReason.ERROR)
            return True
        raise  # Fatal mid-run remains an explicit SA/PO decision, never guessed here.
    try:
        return run_next(
            _ClaimedQueue(session_id),
            store,
            adapter,
            params=copy.model_params,
            repeats=session.repetitions,
            rate_limit_per_minute=limit,
            sleep=sleep,
            clock=clock,
            call_log=billing,
            product_id=copy.product_id,
            before_attempt=store.prepare_attempt,
            after_attempt=store.commit_attempt,
        )
    except ExecutionInterrupted:
        db.rollback()
        if stopping():
            store.mark_status(session_id, SessionStatus.PAUSED, stop_reason=StopReason.RESTART)
        return True
