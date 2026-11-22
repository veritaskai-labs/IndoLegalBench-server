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
from datetime import datetime
from typing import Protocol, cast

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import SessionFacts
from app.modules.reports.models import MeasurementKind, ScoreScope, ScoreSplit
from app.modules.reports.report_scoring import CallCounts, GradedCase, tags_for_split
from app.modules.reports.service import allowed_case_tags
from app.modules.runs.models import SessionType


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
    label: str = ""
    created_at: datetime | None = None
    # Waktu selesai sesi: finished_at untuk sesi otomatis, finalized_at untuk sesi manual.
    # Bulan titik tren diambil dari sini dalam WIB (D4 [KEPUTUSAN SA]).
    finished_at: datetime | None = None

    @property
    def measurement_kind(self) -> MeasurementKind:
        if SessionType(self.facts.session_type) is SessionType.MANUAL:
            return MeasurementKind.MANUAL
        return MeasurementKind.AUTOMATIC


class SessionRecordReader(Protocol):
    """Membaca sesi menurut id. Sesi yang tidak ada tidak ikut dikembalikan."""

    def list_ids(self) -> list[uuid.UUID]: ...

    def read(self, session_ids: Sequence[uuid.UUID]) -> list[SessionRecord]: ...


def facts_for_scope(record: SessionRecord, scope: ScoreScope) -> SessionFacts:
    """Fakta sesi dengan scored_cases disaring ke split yang diminta (AC14)."""
    tags = allowed_case_tags(scope)
    # replace() mengembalikan tipe yang sama; cast hanya memberi tahu analisis statis (Sonar S5886).
    return cast(
        SessionFacts,
        replace(
            record.facts,
            scored_cases=tuple(kasus.ref for kasus in record.graded_cases if kasus.split in tags),
        ),
    )


def calls_for_split(record: SessionRecord, split: ScoreSplit) -> CallCounts:
    """Jumlah upaya pemanggilan untuk kasus-kasus di satu jenis skor (AC2)."""
    tags = tags_for_split(split)
    hitungan = [jumlah for tag, jumlah in record.calls_by_split.items() if tag in tags]
    return CallCounts(
        total=sum(jumlah.total for jumlah in hitungan),
        failed=sum(jumlah.failed for jumlah in hitungan),
    )
