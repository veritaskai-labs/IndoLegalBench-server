"""Bentuk data satu sesi untuk pembuat laporan, dan port pembacanya. PBI-15 SCRUM-79 SCRUM-313.

SessionRecord adalah apa yang pembuat laporan butuhkan dari satu sesi: fakta untuk aturan
perbandingan (SCRUM-301), kasus yang sudah dinilai beserta skornya, dan jumlah upaya
pemanggilan. SessionRecordReader adalah port: pembuat laporan hanya mengenal ini, bukan
database, sehingga tesnya cukup memakai pembaca palsu.

Modul ini murni, tanpa database dan tanpa HTTP.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import SessionFacts
from app.modules.reports.models import MeasurementKind, ScoreScope, ScoreSplit
from app.modules.reports.report_scoring import CallCounts, GradedCase
from app.modules.reports.service import allowed_case_tags
from app.modules.runs.models import SessionType

# Split kasus yang upayanya dihitung untuk tiap jenis skor; gabungan menghitung semuanya.
_TAGS_BY_SPLIT = {
    ScoreSplit.TEST: frozenset({SplitTag.TEST}),
    ScoreSplit.DEV: frozenset({SplitTag.DEV}),
    ScoreSplit.COMBINED: frozenset(SplitTag),
}


@dataclass(frozen=True)
class SessionRecord:
    """Satu sesi, sebagaimana dibaca dari D2.

    facts.scored_cases di sini berisi SEMUA kasus yang sudah dinilai; facts_for_scope yang
    menyaringnya ke split yang diminta (kontrak adapter di comparison_rules).
    calls_by_split menghitung upaya per split kasus, supaya laporan Test tidak ikut
    menghitung upaya kasus dev.
    """

    facts: SessionFacts
    graded_cases: tuple[GradedCase, ...]
    calls_by_split: Mapping[SplitTag, CallCounts]

    @property
    def measurement_kind(self) -> MeasurementKind:
        if SessionType(self.facts.session_type) is SessionType.MANUAL:
            return MeasurementKind.MANUAL
        return MeasurementKind.AUTOMATIC


class SessionRecordReader(Protocol):
    """Membaca sesi menurut id. Sesi yang tidak ada tidak ikut dikembalikan."""

    def read(self, session_ids: Sequence[uuid.UUID]) -> list[SessionRecord]: ...


def facts_for_scope(record: SessionRecord, scope: ScoreScope) -> SessionFacts:
    """Fakta sesi dengan scored_cases disaring ke split yang diminta (AC14)."""
    tags = allowed_case_tags(scope)
    return replace(
        record.facts,
        scored_cases=tuple(kasus.ref for kasus in record.graded_cases if kasus.split in tags),
    )


def calls_for_split(record: SessionRecord, split: ScoreSplit) -> CallCounts:
    """Jumlah upaya pemanggilan untuk kasus-kasus di satu jenis skor (AC2)."""
    tags = _TAGS_BY_SPLIT[split]
    hitungan = [jumlah for tag, jumlah in record.calls_by_split.items() if tag in tags]
    return CallCounts(
        total=sum(jumlah.total for jumlah in hitungan),
        failed=sum(jumlah.failed for jumlah in hitungan),
    )
