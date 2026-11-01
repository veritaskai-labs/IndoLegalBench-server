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
