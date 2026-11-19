"""Hitungan angka laporan: skor, tingkat kegagalan, dan peringkat. PBI-15 SCRUM-79 SCRUM-313.

Murni, tanpa database dan tanpa HTTP. AC2, AC6.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import CaseRef
from app.modules.reports.models import MeasurementKind, ScoreSplit
from app.modules.runs.score_aggregation import raw_mean_score

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)

# Split kasus yang dihitung untuk tiap jenis skor; gabungan memakai semua kasus.
_TAGS_BY_SPLIT: dict[ScoreSplit, frozenset[SplitTag]] = {
    ScoreSplit.TEST: frozenset({SplitTag.TEST}),
    ScoreSplit.DEV: frozenset({SplitTag.DEV}),
    ScoreSplit.COMBINED: frozenset(SplitTag),
}


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
        """Rata-rata biasa skor deterministik dan LLM; kosong bila salah satunya kosong (D1)."""
        if self.det is None or self.llm is None:
            return None
        return (self.det + self.llm) / 2


@dataclass(frozen=True)
class RankEntry:
    session_id: uuid.UUID
    label: str
    score: SplitScore


def tags_for_split(split: ScoreSplit) -> frozenset[SplitTag]:
    return _TAGS_BY_SPLIT[split]


def cases_for_split(cases: Sequence[GradedCase], split: ScoreSplit) -> tuple[GradedCase, ...]:
    """Kasus yang dihitung untuk satu jenis skor: Test, Dev, atau semuanya (gabungan)."""
    tags = tags_for_split(split)
    return tuple(case for case in cases if case.split in tags)


def score_cases(cases: Sequence[GradedCase]) -> SplitScore:
    """Dua jalur dirata-rata terpisah dan tidak dibulatkan; skor kosong dilewati, bukan nol."""
    return SplitScore(
        det=raw_mean_score(case.det for case in cases),
        llm=raw_mean_score(case.llm for case in cases),
    )


def failure_rate(kind: MeasurementKind, calls: CallCounts) -> Decimal | None:
    """Persen upaya yang gagal teknis dari total upaya termasuk coba ulang (AC2).

    Tidak berlaku untuk produk manual. Tanpa satu pun upaya, hasilnya 0, bukan galat.
    """
    if calls.total < 0 or calls.failed < 0 or calls.failed > calls.total:
        raise ValueError(f"jumlah upaya tidak mungkin: {calls.failed} gagal dari {calls.total}")
    if kind is MeasurementKind.MANUAL:
        return None
    if calls.total == 0:
        return Decimal("0.00")
    return (Decimal(calls.failed) * _HUNDRED / Decimal(calls.total)).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )


def rank_products(entries: Sequence[RankEntry]) -> dict[uuid.UUID, int | None]:
    """Peringkat dari skor mentah, bukan yang sudah dibulatkan (AC6).

    Urutannya total menurun, lalu skor LLM menurun, lalu label abjad (keputusan SA, D1).
    Tie-break skor deterministik tidak ada karena tidak pernah tercapai: bila total dan skor
    LLM sama, skor deterministiknya pasti sama. Peringkat urut tanpa lompat dan unik; produk
    tanpa total tidak diberi peringkat dan tidak memakai satu tempat.
    """
    ranked = sorted(
        (entry for entry in entries if entry.score.total is not None),
        key=lambda entry: (-entry.score.total, -entry.score.llm, entry.label),
    )
    peringkat: dict[uuid.UUID, int | None] = {entry.session_id: None for entry in entries}
    for urutan, entry in enumerate(ranked, start=1):
        peringkat[entry.session_id] = urutan
    return peringkat
