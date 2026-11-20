"""Tren skor satu produk untuk satu pengguna. PBI-17 SCRUM-84 SCRUM-331.

Merangkai kebijakan peran, pembaca sesi, dan penghitung tren. Tidak menyentuh HTTP.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from app.modules.reports.models import ScoreScope
from app.modules.reports.session_record import SessionRecordReader
from app.modules.reports.trend import Trend, TrendMetric, TrendRange
from app.shared.security import Role

TREND_PRODUCT_NOT_FOUND = "TREND_PRODUCT_NOT_FOUND"


class ProductCatalog(Protocol):
    """Nama produk menurut id; None bila produk tidak ada atau sudah dihapus."""

    def name_of(self, product_id: uuid.UUID) -> str | None: ...


@dataclass(frozen=True)
class ProductTrend:
    product_id: uuid.UUID
    product_name: str
    scope: ScoreScope
    metric: TrendMetric
    window: TrendRange
    trend: Trend


def _utc_today() -> date:
    return datetime.now(UTC).date()


class TrendService:
    def __init__(
        self,
        reader: SessionRecordReader,
        catalog: ProductCatalog,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self._reader = reader
        self._catalog = catalog
        self._today = today

    def get_trend(
        self,
        *,
        role: Role,
        product_id: uuid.UUID,
        scope: ScoreScope = ScoreScope.TEST,
        metric: TrendMetric = TrendMetric.TOTAL,
        window: TrendRange = TrendRange.ALL,
    ) -> ProductTrend:
        return ProductTrend(
            product_id=product_id,
            product_name="",
            scope=scope,
            metric=metric,
            window=window,
            trend=Trend(points=(), warnings=()),
        )
