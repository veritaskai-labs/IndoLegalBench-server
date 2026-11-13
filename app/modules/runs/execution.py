"""Perakitan satu sesi pengukuran dari tabel. SCRUM-76 SCRUM-249."""

import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.modules.providers import service as providers_service
from app.modules.runs.adapters import ProductAdapter
from app.modules.runs.call_cost import CacheRates
from app.modules.runs.call_log import CallLogService
from app.modules.runs.dependencies import build_session_queue, get_exchange_rate
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.execution_store import ExecutionInterrupted, TableSessionStore
from app.modules.runs.models import (
    AttemptStatus,
    SessionStatus,
    StopReason,
)
from app.modules.runs.pricing import ConfigPriceCatalog
from app.modules.runs.repository import CallLogRepository, ExecutionReadRepository
from app.modules.runs.summary_ports import ProductInfo
from app.modules.runs.worker import run_next
from app.shared.config import get_settings
from app.shared.exceptions import DomainError

logger = logging.getLogger(__name__)

_WORKER_LOCK = 76249
# Longest rate-limit sleep slice: a Jeda/Batalkan during the spacing wait is seen within this
# many seconds instead of after the full 60 / rate_limit_per_minute (SCRUM-267, UAT K4).
_STOP_POLL_SECONDS = 1.0


def _interruptible(sleep, stop_requested):
    """Sleep in short slices; stop before each slice when the session no longer runs.

    run_next's wait loop re-checks the clock after every slice, so the total spacing is unchanged.
    """

    def wait(seconds: float) -> None:
        if stop_requested():
            raise ExecutionInterrupted
        sleep(min(seconds, _STOP_POLL_SECONDS))

    return wait


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


def _frozen_cache_price(copied: object, catalog: Decimal | None) -> Decimal | None:
    """Tarif cache salinan didahulukan. Katalog yang kosong tetap None, tidak 500."""
    if isinstance(copied, Decimal):
        return copied
    return catalog


def _frozen_cache_rates(copy, catalog: ConfigPriceCatalog):
    rates = catalog.cache_rates(copy.provider_type, copy.model_name)
    return CacheRates(
        read=_frozen_cache_price(copy.price_cache_read_usd_per_mtok, rates.read),
        write=_frozen_cache_price(copy.price_cache_write_usd_per_mtok, rates.write),
        long=_frozen_cache_price(copy.price_cache_write_long_usd_per_mtok, rates.long),
    )


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
            settings.cost_model_prices,
            input_prices=settings.cost_model_input_prices,
            cache_read_prices=getattr(settings, "cost_model_cache_read_prices", {}),
            cache_write_prices=getattr(settings, "cost_model_cache_write_prices", {}),
            cache_write_long_prices=getattr(settings, "cost_model_cache_write_long_prices", {}),
        )
        input_price = copy.price_input_usd_per_mtok
        output_price = copy.price_output_usd_per_mtok
        if input_price is None:
            input_price = catalog.usd_per_million_input_tokens(copy.provider_type, copy.model_name)
        if output_price is None:
            output_price = catalog.usd_per_million_output_tokens(
                copy.provider_type, copy.model_name
            )
        cache_rates = _frozen_cache_rates(copy, catalog)
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
            price_cache_read_usd_per_mtok=cache_rates.read,
            price_cache_write_usd_per_mtok=cache_rates.write,
            price_cache_write_long_usd_per_mtok=cache_rates.long,
        )

    def get(self, product_id):
        if product_id != self.product.id:
            return None
        live = self.live_products.get(product_id)
        if live is None:
            return None
        # Prices/model inputs are frozen; the operational budget must honor admin changes.
        return replace(self.product, monthly_budget_idr=live.monthly_budget_idr)


@dataclass(frozen=True)
class _ExecutionOptions:
    adapter_factory: Callable | None
    rates: ExchangeRateProvider | None
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    rate_limit: bool
    stopping: Callable[[], bool]


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
        return _run_claimed(
            db,
            _ExecutionOptions(
                adapter_factory=adapter_factory,
                rates=rates,
                sleep=sleep,
                clock=clock,
                rate_limit=rate_limit,
                stopping=stopping,
            ),
        )


def find_running_session(db):
    """Public runtime read used by the process entrypoint's startup guard."""
    return ExecutionReadRepository(db).running_automatic_id()


def _run_claimed(db, options: _ExecutionOptions):
    session_id = find_running_session(db)
    if session_id is None:
        session_id = build_session_queue(db).take_next()
    if session_id is None:
        return False
    try:
        return _execute_claimed(db, session_id, options)
    except Exception as error:
        # UUID and exception class are safe context; provider text/tracebacks may contain secrets.
        logger.error(
            "Measurement session %s dispatch failed (%s); recovery: docs/MEASUREMENT_WORKER.md",
            session_id,
            type(error).__name__,
        )
        raise


def _execute_claimed(db, session_id, options: _ExecutionOptions):
    repository = ExecutionReadRepository(db)
    session = repository.session(session_id)
    copy = repository.product_copy(session_id)
    store = TableSessionStore(db, session_id, stopping=options.stopping)
    states = repository.attempt_statuses(session_id)
    if AttemptStatus.RUNNING in states:
        raise RuntimeError("uncertain attempt requires recovery before dispatch")
    if AttemptStatus.PENDING not in states:
        # Retrying finalization must not require another credential/provider call or new budget.
        store.mark_status(session_id, SessionStatus.FINISHED)
        return True
    try:
        factory = options.adapter_factory or providers_service.build_measurement_adapter
        adapter = factory(db, copy)
        live = providers_service.get_product(db, copy.product_id)
        # D2 freezes model parameters/prices; rate limit is an operational limit read at dispatch.
        limit = live.rate_limit_per_minute if options.rate_limit else None
        exchange = options.rates or get_exchange_rate()
        exchange.usd_idr()  # Preflight before any paid provider dispatch, not after the response.
        billing = CallLogService(
            products=_FrozenProduct(copy, ProductAdapter(db)),
            repository=CallLogRepository(db),
            rates=exchange,
            clock=lambda: datetime.now(UTC),
        )
    except (DomainError, ValueError):
        if not repository.has_calls(session_id) and all(
            state == AttemptStatus.PENDING for state in states
        ):
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
            sleep=_interruptible(options.sleep, store.stop_requested),
            clock=options.clock,
            call_log=billing,
            product_id=copy.product_id,
            before_attempt=store.prepare_attempt,
            after_attempt=store.commit_attempt,
        )
    except ExecutionInterrupted:
        db.rollback()
        if options.stopping():
            store.mark_status(session_id, SessionStatus.PAUSED, stop_reason=StopReason.RESTART)
        return True
