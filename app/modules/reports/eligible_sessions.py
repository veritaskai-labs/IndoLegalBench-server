"""Daftar sesi beserta kelayakannya untuk laporan. PBI-15 SCRUM-79 SCRUM-313.

Supaya halaman pembuat laporan bisa menandai sesi yang belum layak sebelum pengguna menekan
Buat (dan baru menerima 409). Aturan kelayakannya tetap satu: eligibility_problems dari
SCRUM-301. Layak di sini hanya soal satu sesi; dua sesi layak masih bisa ditolak saat dibuat
bila rubrik, penilai, atau suite-nya berbeda.

Modul ini murni: tanpa database dan tanpa HTTP, hanya mengenal port SessionRecordReader.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    CaseRef,
    eligibility_problems,
    ineligibility_message,
)
from app.modules.reports.models import MeasurementFilter
from app.modules.reports.report_generation import KINDS_BY_FILTER
from app.modules.reports.session_record import SessionRecordReader
from app.modules.runs.models import SessionPurpose, SessionStatus, SessionType


@dataclass(frozen=True)
class IneligibilityReason:
    code: str
    message: str


@dataclass(frozen=True)
class EligibleSession:
    session_id: uuid.UUID
    label: str
    product_id: uuid.UUID
    product_name: str
    created_at: datetime | None
    session_type: SessionType
    purpose: SessionPurpose
    status: SessionStatus
    judge_model_version: str | None
    case_kinds: tuple[SplitTag, ...]
    eligible: bool
    reasons: tuple[IneligibilityReason, ...]


class EligibleSessionLister:
    def __init__(self, reader: SessionRecordReader) -> None:
        self._reader = reader

    def list(
        self, *, measurement_filter: MeasurementFilter = MeasurementFilter.ALL
    ) -> list[EligibleSession]:
        """Semua sesi aktif menurut urutan pembaca, tiap sesi dengan semua alasannya."""
        kinds = KINDS_BY_FILTER[measurement_filter]
        items = []
        for record in self._reader.read(self._reader.list_ids()):
            if record.measurement_kind not in kinds:
                continue
            facts = record.facts
            reasons = tuple(
                IneligibilityReason(code, ineligibility_message(code))
                for code in eligibility_problems(facts)
            )
            items.append(
                EligibleSession(
                    session_id=facts.session_id,
                    label=record.label,
                    product_id=facts.product_id,
                    product_name=facts.product_name,
                    created_at=record.created_at,
                    session_type=SessionType(facts.session_type),
                    purpose=SessionPurpose(facts.purpose),
                    status=SessionStatus(facts.status),
                    judge_model_version=facts.judge_model_version,
                    case_kinds=_case_kinds(facts.snapshot_cases),
                    eligible=not reasons,
                    reasons=reasons,
                )
            )
        return items


def _case_kinds(snapshot_cases: Iterable[CaseRef]) -> tuple[SplitTag, ...]:
    """Jenis kasus (dev, test) yang ada di snapshot sesi, tanpa duplikat, urut menurut SplitTag."""
    present = {SplitTag(ref.split_tag) for ref in snapshot_cases}
    return tuple(tag for tag in SplitTag if tag in present)
