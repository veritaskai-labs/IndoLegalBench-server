"""SCRUM-76 SCRUM-209: one call_logs row per attempt."""

import uuid
from datetime import UTC, datetime, timedelta

from app.modules.runs.call_log import CallLogService
from app.modules.runs.ports import AttemptRecord
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.summary_ports import ProductInfo

PRODUCT_ID = uuid.uuid4()
NOW = datetime(2026, 10, 31, 4, 0, tzinfo=UTC)


class _Products:
    def __init__(self, product: ProductInfo) -> None:
        self.product = product

    def get(self, product_id):
        return self.product if product_id == self.product.id else None


class _Rate:
    def usd_idr(self):
        from decimal import Decimal

        return Decimal("16000")


def _product() -> ProductInfo:
    from decimal import Decimal

    return ProductInfo(
        id=PRODUCT_ID,
        name="Gemini",
        measurement_type="automatic",
        is_active=True,
        provider_type="gemini_interactions",
        model_name="gemini-2.5-flash",
        monthly_budget_idr=Decimal("500000.00"),
        price_output_usd_per_mtok=Decimal("1"),
        price_input_usd_per_mtok=Decimal("1"),
    )


def _record(**ubah) -> AttemptRecord:
    nilai = {
        "session_id": uuid.uuid4(),
        "case_index": 0,
        "repeat_index": 0,
        "prompt": "pasal?",
        "text": "jawab",
        "model_version": "gemini-2.5-flash",
        "latency_ms": 10,
        "raw": {"status": "completed"},
        "error": None,
        "failure_kind": None,
        "input_tokens": 8,
        "output_tokens": 1,
    }
    nilai.update(ubah)
    return AttemptRecord(**nilai)


def _service(db_session) -> CallLogService:
    return CallLogService(
        products=_Products(_product()),
        repository=CallLogRepository(db_session),
        rates=_Rate(),
        clock=lambda: NOW,
    )


def test_null_tokens_stay_null(db_session):
    row = _service(db_session).record(
        PRODUCT_ID,
        _record(input_tokens=None, output_tokens=None),
        NOW,
        NOW + timedelta(milliseconds=12),
    )
    assert row.input_tokens is None
    assert row.output_tokens is None
    assert row.attempt_id is not None
    assert row.duration_ms == 12


def test_zero_tokens_stay_zero(db_session):
    row = _service(db_session).record(
        PRODUCT_ID,
        _record(input_tokens=0, output_tokens=0),
        NOW,
        NOW,
    )
    assert row.input_tokens == 0
    assert row.output_tokens == 0


def test_cost_is_tokens_times_price_times_rate(db_session):
    from decimal import Decimal

    row = _service(db_session).record(
        PRODUCT_ID,
        _record(input_tokens=1_000_000, output_tokens=2_000_000),
        NOW,
        NOW,
    )
    assert row.cost_idr == Decimal("48000.00")
    assert row.usd_idr_rate == Decimal("16000")


def test_missing_token_side_is_not_billed_as_zero(db_session):
    from decimal import Decimal

    row = _service(db_session).record(
        PRODUCT_ID,
        _record(input_tokens=1_000_000, output_tokens=None),
        NOW,
        NOW,
    )
    assert row.output_tokens is None
    assert row.cost_idr == Decimal("16000.00")


def test_technical_failure_is_stored_and_a_wrong_answer_is_not(db_session):
    from app.modules.runs.call_log import technical_result

    failed = _service(db_session).record(
        PRODUCT_ID,
        _record(text="", failure_kind="provider_error", raw={"status": 429, "usage": {"n": 1}}),
        NOW,
        NOW,
    )
    assert failed.failure_kind == "provider_error"
    assert failed.result == "provider_error"
    assert failed.raw == {"status": 429, "usage": {"n": 1}}
    wrong = _record(text="jawaban salah", failure_kind=None)
    assert technical_result(wrong) == ("ok", None)


def test_budget_stop_keeps_finished_rows_and_does_not_notify(db_session):
    from decimal import Decimal

    from app.modules.providers.adapters.base import ProviderResponse
    from app.modules.runs.worker import run_session

    product = _product()
    product_small = ProductInfo(
        id=product.id,
        name=product.name,
        measurement_type=product.measurement_type,
        is_active=product.is_active,
        provider_type=product.provider_type,
        model_name=product.model_name,
        monthly_budget_idr=Decimal("1.00"),
        price_output_usd_per_mtok=Decimal("1"),
        price_input_usd_per_mtok=Decimal("1"),
    )
    logs = CallLogService(
        products=_Products(product_small),
        repository=CallLogRepository(db_session),
        rates=_Rate(),
        clock=lambda: NOW,
    )
    notes = []

    class Ask:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, prompt, *, parameters=None, timeout=240.0):
            self.calls += 1
            return ProviderResponse(
                text="jawab",
                model_version="m",
                latency_ms=1,
                raw={},
                output_tokens=1_000_000,
                input_tokens=0,
            )

    class Store:
        def __init__(self) -> None:
            self.attempts = []

        def list_cases(self, session_id):
            return ["satu", "dua"]

        def save_attempt(self, record):
            self.attempts.append(record)

        def update_progress(self, session_id, *, completed, failed):
            return None

        def mark_status(self, session_id, status):
            return None

    ask = Ask()
    store = Store()
    run_session(uuid.uuid4(), store, ask, repeats=1, call_log=logs, product_id=PRODUCT_ID)
    assert ask.calls == 1
    assert len(store.attempts) == 1
    assert logs.allows(PRODUCT_ID) is False
    assert notes == []
