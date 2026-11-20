"""Tren skor satu produk antar bulan. PBI-17 SCRUM-84 SCRUM-331.

Murni, tanpa database dan tanpa HTTP: menerima rekaman sesi yang sudah dibaca pembaca sesi
(SessionRecordReader) dan menghasilkan titik tren. AC1 sampai AC6, AC11, AC12, AC13.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from app.modules.reports.comparison_rules import is_eligible
from app.modules.reports.models import ScoreScope, ScoreSplit
from app.modules.reports.report_scoring import SplitScore, cases_for_split, score_cases
from app.modules.reports.session_record import SessionRecord
from app.modules.runs.score_aggregation import round_score


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


# Jenis kasus yang dipilih pengguna menentukan baris skor mana yang digambar (AC3, AC12).
_SPLIT_FOR_SCOPE: dict[ScoreScope, ScoreSplit] = {
    ScoreScope.TEST: ScoreSplit.TEST,
    ScoreScope.DEV: ScoreSplit.DEV,
    ScoreScope.DEV_TEST: ScoreSplit.COMBINED,
}

# Tabel metrik, supaya metrik baru cukup satu baris di sini (AC3).
_METRIC_VALUE: dict[TrendMetric, Callable[[SplitScore], Decimal | None]] = {
    TrendMetric.TOTAL: lambda skor: skor.total,
    TrendMetric.DETERMINISTIC: lambda skor: skor.det,
    TrendMetric.LLM: lambda skor: skor.llm,
}


def build_trend(
    records: Sequence[SessionRecord],
    *,
    scope: ScoreScope,
    metric: TrendMetric,
    window: TrendRange,
    today: date,
) -> Trend:
    """Satu titik per bulan dari sesi layak terbaru bulan itu (AC1, AC3, AC4, AC6).

    Bulan tanpa sesi layak tidak punya titik, jadi produk baru otomatis mulai dari bulan
    pertama diukur. Skor dibulatkan hanya di sini, saat dipaparkan ke pemanggil.
    """
    latest = _latest_eligible_per_month(records)
    points = tuple(
        _point(month, latest[month], scope=scope, metric=metric) for month in sorted(latest)
    )
    return Trend(points=points, warnings=())


def _as_utc(moment: datetime) -> datetime:
    """SQLite mengembalikan datetime tanpa zona; bacaannya UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _latest_eligible_per_month(records: Sequence[SessionRecord]) -> dict[date, SessionRecord]:
    # Id memutus seri waktu yang sama supaya hasilnya selalu sama.
    newest: dict[date, tuple[tuple[datetime, uuid.UUID], SessionRecord]] = {}
    for record in records:
        if record.created_at is None or not is_eligible(record.facts):
            continue
        created = _as_utc(record.created_at)
        month = created.date().replace(day=1)
        recency = (created, record.facts.session_id)
        if month not in newest or recency > newest[month][0]:
            newest[month] = (recency, record)
    return {month: record for month, (_, record) in newest.items()}


def _point(
    month: date, record: SessionRecord, *, scope: ScoreScope, metric: TrendMetric
) -> TrendPoint:
    cases = cases_for_split(record.graded_cases, _SPLIT_FOR_SCOPE[scope])
    value = round_score(_METRIC_VALUE[metric](score_cases(cases)))
    return TrendPoint(
        month=month,
        session_id=record.facts.session_id,
        label=record.label,
        value=value,
        breaks=(),
        markers=(),
    )
