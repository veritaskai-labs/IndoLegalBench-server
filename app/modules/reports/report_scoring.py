"""Hitungan angka laporan: skor, tingkat kegagalan, dan peringkat. PBI-15 SCRUM-79 SCRUM-313.

Murni, tanpa database dan tanpa HTTP. AC2, AC6.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import CaseRef
from app.modules.reports.models import MeasurementKind, ScoreSplit


@dataclass(frozen=True)
class GradedCase:
    """Satu kasus yang sudah dinilai di satu sesi, skornya rata-rata semua percobaan."""

    ref: CaseRef
    split: SplitTag
    det: Decimal | None
    llm: Decimal | None


@dataclass(frozen=True)
class CallCounts:
    """Jumlah upaya pemanggilan produk, termasuk percobaan ulang (AC2)."""

    total: int
    failed: int


@dataclass(frozen=True)
class SplitScore:
    """Skor mentah (belum dibulatkan) satu sesi untuk satu jenis skor."""

    det: Decimal | None
    llm: Decimal | None

    @property
    def total(self) -> Decimal | None:
        raise NotImplementedError


@dataclass(frozen=True)
class RankEntry:
    session_id: uuid.UUID
    label: str
    score: SplitScore


def cases_for_split(cases: Sequence[GradedCase], split: ScoreSplit) -> tuple[GradedCase, ...]:
    raise NotImplementedError


def score_cases(cases: Sequence[GradedCase]) -> SplitScore:
    raise NotImplementedError


def failure_rate(kind: MeasurementKind, calls: CallCounts) -> Decimal | None:
    raise NotImplementedError


def rank_products(entries: Sequence[RankEntry]) -> dict[uuid.UUID, int | None]:
    raise NotImplementedError
