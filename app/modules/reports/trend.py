"""Tren skor satu produk antar bulan. PBI-17 SCRUM-84 SCRUM-331.

Murni, tanpa database dan tanpa HTTP: menerima rekaman sesi yang sudah dibaca pembaca sesi
(SessionRecordReader) dan menghasilkan titik tren. AC1 sampai AC6, AC11, AC12, AC13.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast

from app.modules.reports.comparison_rules import (
    MODEL_VERSION_DIFFERS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    SessionFacts,
    comparison_markers,
    comparison_rejections,
    is_eligible,
    session_markers,
    shared_cases,
)
from app.modules.reports.models import ScoreScope, ScoreSplit
from app.modules.reports.report_scoring import SplitScore, cases_for_split, score_cases
from app.modules.reports.service import allowed_case_tags
from app.modules.reports.session_record import SessionRecord
from app.modules.reports.wib import WIB
from app.modules.runs.models import SessionType
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
    # Jumlah kasus yang sama pada segmen garis titik ini (D4: "jumlah kasus tiap segmen").
    case_count: int


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


# Jumlah bulan kalender terakhir (termasuk bulan ini) untuk tiap rentang; ALL tidak terbatas.
_MONTHS_IN_RANGE = {
    TrendRange.THREE_MONTHS: 3,
    TrendRange.SIX_MONTHS: 6,
    TrendRange.TWELVE_MONTHS: 12,
}


def build_trend(
    records: Sequence[SessionRecord],
    *,
    scope: ScoreScope,
    metric: TrendMetric,
    window: TrendRange,
    today: date,
) -> Trend:
    """Satu titik per bulan dari sesi layak terbaru bulan itu (AC1 sampai AC6, AC11).

    Bulan tanpa sesi layak tidak punya titik, jadi produk baru otomatis mulai dari bulan
    pertama diukur. Rentang dipotong lebih dulu, sehingga bulan di luar rentang tidak ikut
    membatasi kasus yang sama dan tidak memutus titik pertama. Skor dibulatkan hanya di
    sini, saat dipaparkan ke pemanggil.
    """
    first_month = _first_month(window, today)
    latest = {
        month: record
        for month, record in _latest_eligible_per_month(records).items()
        if first_month is None or month >= first_month
    }
    ordered = [latest[month] for month in sorted(latest)]
    facts = [_scoped_facts(record, scope) for record in ordered]
    breaks = [() if i == 0 else _breaks_between(facts[i - 1], fact) for i, fact in enumerate(facts)]
    shared, nothing_shared = _shared_case_keys(facts, breaks)
    repetitions_differ = len({fact.repetitions for fact in facts}) > 1
    points = tuple(
        _point(
            month,
            record,
            scope=scope,
            metric=metric,
            breaks=cuts,
            markers=_markers(fact, repetitions_differ),
            case_keys=keys,
        )
        for month, record, fact, cuts, keys in zip(
            sorted(latest), ordered, facts, breaks, shared, strict=True
        )
    )
    return Trend(points=points, warnings=(NO_SHARED_CASES,) if nothing_shared else ())


def _first_month(window: TrendRange, today: date) -> date | None:
    """Bulan pertama yang masih masuk rentang (AC2); None berarti semua sesi."""
    months = _MONTHS_IN_RANGE.get(window)
    if months is None:
        return None
    index = today.year * 12 + today.month - 1 - (months - 1)
    return date(index // 12, index % 12 + 1, 1)


def _shared_case_keys(
    facts: Sequence[SessionFacts], breaks: Sequence[tuple[str, ...]]
) -> tuple[list[frozenset[tuple[uuid.UUID, uuid.UUID]]], bool]:
    """Kasus yang dijawab semua sesi pada garis yang sama, satu himpunan per titik (AC11).

    Garis yang terputus (breaks tidak kosong) menjadi segmen baru dengan irisannya sendiri,
    jadi perubahan versi tidak mengosongkan segmen lain. Segmen dengan dua titik atau lebih
    yang irisannya kosong tidak digambar dan membuat peringatan; satu titik sendirian tidak
    pernah diperingatkan.
    """
    starts = [i for i, cuts in enumerate(breaks) if i == 0 or cuts] + [len(facts)]
    keys: list[frozenset[tuple[uuid.UUID, uuid.UUID]]] = []
    nothing_shared = False
    for begin, end in zip(starts, starts[1:], strict=False):
        shared = shared_cases(facts[begin:end])
        nothing_shared = nothing_shared or (end - begin > 1 and not shared)
        segment_keys = frozenset((case.case_id, case.case_version_id) for case in shared)
        keys.extend([segment_keys] * (end - begin))
    return keys, nothing_shared


def _scoped_facts(record: SessionRecord, scope: ScoreScope) -> SessionFacts:
    """Fakta sesi yang hanya memuat kasus pada jenis kasus yang digambar (AC12).

    Perubahan pada kasus di luar jenis itu tidak boleh memutus garisnya.
    """
    tags = {tag.value for tag in allowed_case_tags(scope)}
    facts = record.facts
    # replace() mengembalikan tipe yang sama; cast hanya memberi tahu analisis statis (Sonar S5886).
    return cast(
        SessionFacts,
        replace(
            facts,
            scored_cases=tuple(
                graded.ref for graded in record.graded_cases if graded.split.value in tags
            ),
            snapshot_cases=tuple(ref for ref in facts.snapshot_cases if ref.split_tag in tags),
        ),
    )


def _breaks_between(before: SessionFacts, after: SessionFacts) -> tuple[str, ...]:
    """Alasan garis diputus antara dua titik berurutan (AC5, AC13).

    Syaratnya sama dengan perbandingan PBI-15: versi rubrik, model penilai, dan suite atau
    versi kasus, ditambah versi model produk. Jumlah percobaan berbeda tidak memutus garis,
    hanya menjadi penanda.
    """
    reasons = [rejection.requirement for rejection in comparison_rejections((before, after))]
    if MODEL_VERSION_DIFFERS in comparison_markers((before, after)):
        reasons.append(MODEL_VERSION_DIFFERS)
    return tuple(reasons)


def _markers(facts: SessionFacts, repetitions_differ: bool) -> tuple[str, ...]:
    markers = session_markers(facts)
    if repetitions_differ and SessionType(facts.session_type) is SessionType.MANUAL:
        markers += (REPETITIONS_DIFFER,)
    return markers


def _as_utc(moment: datetime) -> datetime:
    """SQLite mengembalikan datetime tanpa zona; bacaannya UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _latest_eligible_per_month(records: Sequence[SessionRecord]) -> dict[date, SessionRecord]:
    """Sesi layak yang selesai paling akhir di tiap bulan WIB (AC4, D4 [KEPUTUSAN SA]).

    Bulan dan "terbaru" diambil dari waktu selesai sesi, bukan waktu dibuat atau dinilai,
    jadi nilai ulang rubrik tidak memindahkan sesi ke bulan lain.
    """
    # Id memutus seri waktu yang sama supaya hasilnya selalu sama.
    newest: dict[date, tuple[tuple[datetime, uuid.UUID], SessionRecord]] = {}
    for record in records:
        if record.finished_at is None or not is_eligible(record.facts):
            continue
        finished = _as_utc(record.finished_at)
        month = finished.astimezone(WIB).date().replace(day=1)
        recency = (finished, record.facts.session_id)
        if month not in newest or recency > newest[month][0]:
            newest[month] = (recency, record)
    return {month: record for month, (_, record) in newest.items()}


def _point(
    month: date,
    record: SessionRecord,
    *,
    scope: ScoreScope,
    metric: TrendMetric,
    breaks: tuple[str, ...],
    markers: tuple[str, ...],
    case_keys: frozenset[tuple[uuid.UUID, uuid.UUID]],
) -> TrendPoint:
    cases = tuple(
        case
        for case in cases_for_split(record.graded_cases, _SPLIT_FOR_SCOPE[scope])
        if (case.ref.case_id, case.ref.case_version_id) in case_keys
    )
    value = round_score(_METRIC_VALUE[metric](score_cases(cases)))
    return TrendPoint(
        month=month,
        session_id=record.facts.session_id,
        label=record.label,
        value=value,
        breaks=breaks,
        markers=markers,
        case_count=len(case_keys),
    )
