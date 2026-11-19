"""Bentuk data satu sesi untuk pembuat laporan, dan port pembacanya. PBI-15 SCRUM-79 SCRUM-313.

SessionRecord adalah apa yang pembuat laporan butuhkan dari satu sesi: fakta untuk aturan
perbandingan (SCRUM-301), kasus yang sudah dinilai beserta skornya, dan jumlah upaya
pemanggilan. SessionRecordReader adalah port: pembuat laporan hanya mengenal ini, bukan
database, sehingga tesnya cukup memakai pembaca palsu.

Modul ini murni, tanpa database dan tanpa HTTP.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import SessionFacts
from app.modules.reports.models import MeasurementKind, ScoreScope, ScoreSplit
from app.modules.reports.report_scoring import CallCounts, GradedCase


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
        raise NotImplementedError


class SessionRecordReader(Protocol):
    """Membaca sesi menurut id. Sesi yang tidak ada tidak ikut dikembalikan."""

    def read(self, session_ids: Sequence[uuid.UUID]) -> list[SessionRecord]: ...


def facts_for_scope(record: SessionRecord, scope: ScoreScope) -> SessionFacts:
    raise NotImplementedError


def calls_for_split(record: SessionRecord, split: ScoreSplit) -> CallCounts:
    raise NotImplementedError
