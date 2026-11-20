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
from app.modules.reports.trend import Trend, TrendMetric, TrendRange, build_trend
from app.modules.reports.trend_policy import ensure_can_view_trend
from app.shared.exceptions import NotFoundError
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
        # Peran diperiksa lebih dulu: permintaan skor dev yang ditolak tidak boleh membaca sesi.
        ensure_can_view_trend(role, scope)
        name = self._catalog.name_of(product_id)
        if name is None:
            raise NotFoundError("Produk AI tidak ditemukan.", code=TREND_PRODUCT_NOT_FOUND)

        # Hanya membaca hasil yang sudah tersimpan; tidak ada pengukuran yang dijalankan ulang.
        records = [
            record
            for record in self._reader.read(self._reader.list_ids())
            if record.facts.product_id == product_id
        ]
        trend = build_trend(records, scope=scope, metric=metric, window=window, today=self._today())
        return ProductTrend(
            product_id=product_id,
            product_name=name,
            scope=scope,
            metric=metric,
            window=window,
            trend=trend,
        )
