"""Validasi langsung (dry-run) pilihan sesi untuk laporan. PBI-15 SCRUM-79 SCRUM-362.

AC1, AC4, AC5, AC13, AC14. Menjawab "apa yang akan ditolak POST /reports" saat sesi dipilih,
bukan setelah klik Buat Laporan: semua pelanggaran sekaligus, peringatan yang tidak memblokir,
dan jenis nilai yang tersedia. Tidak menyimpan apa pun dan tidak mengenal HTTP.

Aturannya bukan salinan: sesi dibaca dan direncanakan lewat report_planning, langkah yang sama
dengan ReportGenerator. POST /reports tetap memeriksa ulang di server.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.cases.models import SplitTag
from app.modules.reports.comparison_rules import (
    DUPLICATE_PRODUCT,
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    NEED_AT_LEAST_TWO_PRODUCTS,
    NO_SHARED_CASES,
    REPETITIONS_DIFFER,
    TRIAL_SESSION,
    ComparisonPlan,
    ineligibility_message,
    shared_cases,
)
from app.modules.reports.creation_policy import available_scopes, ensure_can_use_scope
from app.modules.reports.models import MeasurementFilter, ScoreScope
from app.modules.reports.report_planning import load_records, plan_for_scope, rejection_for_role
from app.modules.reports.session_record import SessionRecord, SessionRecordReader, facts_for_scope
from app.shared.security import Role


class ViolationKind(StrEnum):
    SELECTION = "selection"
    SESSION = "session"
    REQUIREMENT = "requirement"
    CASE_SLICE = "case_slice"


_SELECTION_MESSAGES = {
    NEED_AT_LEAST_TWO_PRODUCTS: "Pilih sesi dari minimal dua produk untuk membuat perbandingan.",
    DUPLICATE_PRODUCT: "Satu produk hanya boleh diwakili satu sesi, dan satu sesi hanya sekali.",
}

_NO_SHARED_CASES_MESSAGE = (
    "Tidak ada kasus yang dijawab semua produk yang dipilih, jadi tidak ada angka perbandingan."
)

_WARNING_MESSAGES = {
    REPETITIONS_DIFFER: "Jumlah percobaan antar sesi berbeda.",
    MANUAL_ANSWER_RANGE_OVER_LIMIT: "Rentang tanggal jawaban sesi manual ini lebih dari 7 hari.",
    MODEL_VERSION_DIFFERS: "Versi model produk antar sesi berbeda.",
    TRIAL_SESSION: "Sesi ini berjenis Uji Coba.",
}


@dataclass(frozen=True)
class Violation:
    """Satu hal yang akan membuat POST /reports menolak. session_id hanya untuk jenis SESSION."""

    kind: ViolationKind
    code: str
    message: str
    session_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ValidationWarning:
    """Penanda yang tidak memblokir. session_id kosong bila berlaku untuk seluruh perbandingan."""

    code: str
    message: str
    session_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ReportValidation:
    violations: tuple[Violation, ...]
    warnings: tuple[ValidationWarning, ...]
    available_scopes: tuple[ScoreScope, ...]

    @property
    def valid(self) -> bool:
        return not self.violations


class ReportValidator:
    def __init__(self, reader: SessionRecordReader) -> None:
        self._reader = reader

    def validate(
        self,
        *,
        role: Role,
        session_ids: Sequence[uuid.UUID],
        score_scope: ScoreScope,
        measurement_filter: MeasurementFilter,
    ) -> ReportValidation:
        """Periksa satu pilihan sesi tanpa menyimpan apa pun.

        Hak akses atas jenis nilai diperiksa lebih dulu (403), sebelum sesi dibaca, sama
        dengan POST /reports. Sesi yang tidak ada tetap 404; pilihan kosong atau satu sesi
        bukan galat, melainkan pelanggaran, karena halaman memanggil ini tiap pilihan berubah.
        """
        ensure_can_use_scope(role, score_scope)
        records = load_records(self._reader, session_ids, measurement_filter)
        plan = plan_for_scope(records, score_scope)
        eligible = [r for r in records if r.facts.session_id not in plan.ineligible]
        slice_is_empty = len(eligible) >= 2 and not _shared(eligible, score_scope)
        return ReportValidation(
            violations=_violations(plan, role, slice_is_empty),
            warnings=_warnings(plan),
            available_scopes=available_scopes(role, _shared_kinds(eligible)),
        )


def _shared(records: Sequence[SessionRecord], scope: ScoreScope):
    return shared_cases([facts_for_scope(record, scope) for record in records])


def _shared_kinds(eligible: Sequence[SessionRecord]) -> set[SplitTag]:
    """Jenis kasus (dev/test) pada irisan sesi layak, tanpa menyaring jenis nilai."""
    return {SplitTag(case.split_tag) for case in _shared(eligible, ScoreScope.DEV_TEST)}


def _violations(plan: ComparisonPlan, role: Role, slice_is_empty: bool) -> tuple[Violation, ...]:
    found = [
        Violation(ViolationKind.SELECTION, code, _SELECTION_MESSAGES[code])
        for code in plan.selection_errors
    ]
    found += [
        Violation(ViolationKind.SESSION, code, ineligibility_message(code), session_id)
        for session_id, codes in plan.ineligible.items()
        for code in codes
    ]
    found += [
        Violation(ViolationKind.REQUIREMENT, shown.requirement, shown.message)
        for shown in (rejection_for_role(r, role) for r in plan.rejections)
    ]
    if slice_is_empty:
        found.append(Violation(ViolationKind.CASE_SLICE, NO_SHARED_CASES, _NO_SHARED_CASES_MESSAGE))
    return tuple(found)


def _warnings(plan: ComparisonPlan) -> tuple[ValidationWarning, ...]:
    found = [ValidationWarning(code, _WARNING_MESSAGES[code]) for code in plan.markers]
    found += [
        ValidationWarning(code, _WARNING_MESSAGES[code], session_id)
        for session_id, codes in plan.session_markers.items()
        for code in codes
    ]
    return tuple(found)
