"""Simpan biaya usage yang sudah di-stub. Produk dan repositori juga stub.

Tidak ada panggilan penyedia. AttemptRecord membawa bagian token yang
normalizer baca dari respons yang di-stub.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.modules.runs.call_log import CallLogService
from app.modules.runs.ports import AttemptRecord

NOW = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


class _Products:
    def __init__(self, product) -> None:
        self.product = product

    def get(self, product_id):
        return self.product if product_id == PRODUCT_ID else None


class _Rates:
    def usd_idr(self) -> Decimal:
        return Decimal("10000")


class _Repo:
    def __init__(self) -> None:
        self.row = None

    def add(self, row) -> None:
        self.row = row

    def next_try_no(self, attempt_id) -> int:
        return 1


def test_stubbed_usage_is_stored_and_priced_in_parts():
    """Positif: cache ditagih dengan tarifnya, dan bagian token tersimpan apa adanya.

    input_tokens judul 100 tidak ditagih lagi di samping 15 + 80 + 5.
    """
    product = SimpleNamespace(
        id=PRODUCT_ID,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("3"),
        price_cache_read_usd_per_mtok=Decimal("0.1"),
        price_cache_write_usd_per_mtok=Decimal("1.25"),
        price_cache_write_long_usd_per_mtok=None,
        monthly_budget_idr=Decimal("500000.00"),
    )
    repo = _Repo()
    service = CallLogService(
        products=_Products(product),
        repository=repo,
        rates=_Rates(),
        clock=lambda: NOW,
    )
    record = AttemptRecord(
        session_id=uuid.uuid4(),
        case_index=0,
        repeat_index=0,
        prompt="pasal?",
        text="jawab",
        model_version="aiyu-1",
        latency_ms=10,
        raw={"usage": "stub"},
        error=None,
        failure_kind=None,
        input_tokens=100,
        output_tokens=9,
        uncached_input_tokens=15,
        cache_read_tokens=80,
        cache_write_tokens=5,
        cache_write_long_tokens=None,
    )

    service.record(PRODUCT_ID, record, NOW, NOW)

    row = repo.row
    assert row.input_tokens == 100
    assert row.output_tokens == 9
    assert row.uncached_input_tokens == 15
    assert row.cache_read_tokens == 80
    assert row.cache_write_tokens == 5
    assert row.cache_write_long_tokens is None
    assert row.cost_idr == Decimal("0.56")
