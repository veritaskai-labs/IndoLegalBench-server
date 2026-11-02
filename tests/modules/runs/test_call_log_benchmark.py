"""Waktu jumlah bulanan dan sisipan call_logs."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytest.importorskip("pytest_benchmark")

from app.modules.runs.models import CallLog
from app.modules.runs.repository import CallLogRepository

ROWS = 3000
PRODUCT_ID = uuid.uuid4()
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _row() -> CallLog:
    return CallLog(
        attempt_id=uuid.uuid4(),
        product_id=PRODUCT_ID,
        started_at=NOW,
        ended_at=NOW,
        duration_ms=1,
        input_tokens=8,
        output_tokens=1,
        cost_idr=Decimal("1.00"),
        usd_idr_rate=Decimal("16000"),
        model_version="m",
        result="ok",
        failure_kind=None,
        raw={},
    )


def test_monthly_sum(benchmark, db_session):
    repo = CallLogRepository(db_session)
    for _ in range(ROWS):
        repo.add(_row())

    def _sum():
        return repo.sum_cost_since(PRODUCT_ID, NOW)

    assert benchmark(_sum) == Decimal(ROWS)


def test_insert(benchmark, db_session):
    repo = CallLogRepository(db_session)

    def _insert():
        repo.add(_row())

    benchmark(_insert)
