"""Tren skor satu produk antar bulan. PBI-17 SCRUM-84 SCRUM-331.

Murni, tanpa database dan tanpa HTTP: menerima rekaman sesi yang sudah dibaca pembaca sesi
(SessionRecordReader) dan menghasilkan titik tren. AC1 sampai AC6, AC11, AC12, AC13.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.modules.reports.models import ScoreScope
from app.modules.reports.session_record import SessionRecord


class TrendMetric(StrEnum):
    """Satu garis untuk satu metrik; skor tidak digabung (AC3)."""

    TOTAL = "total"
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class TrendRange(StrEnum):
    """Rentang bulan kalender terakhir; bawaannya semua sesi (AC2)."""

    THREE_MONTHS = "3m"
    SIX_MONTHS = "6m"
    TWELVE_MONTHS = "12m"
    ALL = "all"


@dataclass(frozen=True)
class TrendPoint:
    month: date
    session_id: uuid.UUID
    label: str
    value: Decimal | None
    breaks: tuple[str, ...]
    markers: tuple[str, ...]


@dataclass(frozen=True)
class Trend:
    points: tuple[TrendPoint, ...]
    warnings: tuple[str, ...]


def build_trend(
    records: Sequence[SessionRecord],
    *,
    scope: ScoreScope,
    metric: TrendMetric,
    window: TrendRange,
    today: date,
) -> Trend:
    return Trend(points=(), warnings=())
