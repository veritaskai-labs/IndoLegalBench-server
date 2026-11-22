"""Langkah bersama sebelum laporan dibuat: baca sesi, lalu terapkan aturan perbandingan.

PBI-15 SCRUM-79 SCRUM-362. Dipakai ReportGenerator (POST /reports) dan ReportValidator
(POST /reports/validate), supaya dry-run dan pembuatan sungguhan tidak bisa berbeda
(AC13, "POST memakai logika yang sama"). Tidak ada hak akses, penyimpanan, atau HTTP di sini.
"""

import uuid
from collections.abc import Sequence

from app.modules.reports.comparison_rules import (
    SUITE_VERSION,
    ComparisonPlan,
    Rejection,
    plan_comparison,
)
from app.modules.reports.creation_policy import can_see_case_codes
from app.modules.reports.models import MeasurementFilter, MeasurementKind, ScoreScope
from app.modules.reports.session_record import (
    SessionRecord,
    SessionRecordReader,
    facts_for_scope,
)
from app.shared.exceptions import NotFoundError
from app.shared.security import Role

REPORT_SESSION_NOT_FOUND = "REPORT_SESSION_NOT_FOUND"

KINDS_BY_FILTER = {
    MeasurementFilter.AUTOMATIC: frozenset({MeasurementKind.AUTOMATIC}),
    MeasurementFilter.MANUAL: frozenset({MeasurementKind.MANUAL}),
    MeasurementFilter.ALL: frozenset(MeasurementKind),
}

_SUITE_VERSION_WITHOUT_CODES = (
    "Kasus pada snapshot suite berbeda versi atau komposisi dalam split yang sama."
)


def load_records(
    reader: SessionRecordReader,
    session_ids: Sequence[uuid.UUID],
    measurement_filter: MeasurementFilter,
) -> list[SessionRecord]:
    """Baca sesi yang dipilih. 404 bila ada yang tidak ditemukan, lalu saring otomatis/manual."""
    records = reader.read(session_ids)
    found = {record.facts.session_id for record in records}
    missing = [sid for sid in dict.fromkeys(session_ids) if sid not in found]
    if missing:
        raise NotFoundError(
            "Ada sesi yang dipilih tidak ditemukan.",
            code=REPORT_SESSION_NOT_FOUND,
            details={"missing": [str(sid) for sid in missing]},
        )
    kinds = KINDS_BY_FILTER[measurement_filter]
    return [record for record in records if record.measurement_kind in kinds]


def plan_for_scope(records: Sequence[SessionRecord], scope: ScoreScope) -> ComparisonPlan:
    """Aturan perbandingan (SCRUM-301) atas sesi yang sudah disaring ke jenis nilai."""
    return plan_comparison([facts_for_scope(record, scope) for record in records])


def rejection_for_role(rejection: Rejection, role: Role) -> Rejection:
    """Pesan syarat suite memuat kode kasus; peran yang tidak boleh melihatnya dapat pesan umum."""
    if rejection.requirement == SUITE_VERSION and not can_see_case_codes(role):
        return Rejection(rejection.requirement, _SUITE_VERSION_WITHOUT_CODES)
    return rejection
