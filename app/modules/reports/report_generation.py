"""Pembuat laporan perbandingan. PBI-15 SCRUM-79 SCRUM-313.

AC1, AC2, AC6, AC7, AC9, AC12, AC14. Mengurutkan langkahnya saja: hak akses, baca sesi,
aturan perbandingan (SCRUM-301), hitung angka, simpan beku (SCRUM-300). Aturannya sendiri
hidup di modul masing-masing.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.cases.models import SplitTag
from app.modules.reports import service as reports_service
from app.modules.reports.comparison_rules import (
    MANUAL_ANSWER_RANGE_OVER_LIMIT,
    MODEL_VERSION_DIFFERS,
    REPETITIONS_DIFFER,
    ComparisonPlan,
)
from app.modules.reports.creation_policy import ensure_can_create
from app.modules.reports.models import (
    MeasurementFilter,
    ProductLabelMode,
    Report,
    ReportCaseBasis,
    ReportLanguage,
    ReportMarkerCode,
    ReportResult,
    ReportSession,
    ScoreScope,
    ScoreSplit,
)
from app.modules.reports.product_labels import product_label
from app.modules.reports.report_planning import (
    load_records,
    plan_for_scope,
    rejection_for_role,
)
from app.modules.reports.report_scoring import (
    GradedCase,
    RankEntry,
    SplitScore,
    cases_for_split,
    failure_rate,
    rank_products,
    score_cases,
)
from app.modules.reports.session_record import (
    SessionRecord,
    SessionRecordReader,
    calls_for_split,
)
from app.modules.runs.models import SessionPurpose
from app.modules.runs.score_aggregation import round_score
from app.shared.exceptions import ConflictError
from app.shared.security import Role

REPORT_NOT_COMPARABLE = "REPORT_NOT_COMPARABLE"

# Penanda tingkat perbandingan dan tingkat sesi (SCRUM-301) ke kode D1. Uji Coba sengaja tidak
# ada: ia hanya disimpan di report_sessions.is_trial (satu sumber).
_MARKER_CODES = {
    MODEL_VERSION_DIFFERS: ReportMarkerCode.MODEL_VERSION_DIFFERS,
    REPETITIONS_DIFFER: ReportMarkerCode.REPETITIONS_DIFFER,
    MANUAL_ANSWER_RANGE_OVER_LIMIT: ReportMarkerCode.MANUAL_DATE_SPAN_OVER_7_DAYS,
}

_SPLIT_ORDER = tuple(ScoreSplit)


@dataclass(frozen=True)
class ReportRequest:
    title: str
    session_ids: tuple[uuid.UUID, ...]
    score_scope: ScoreScope
    measurement_filter: MeasurementFilter = MeasurementFilter.ALL
    product_label_mode: ProductLabelMode = ProductLabelMode.REAL_NAME
    language: ReportLanguage = ReportLanguage.EN


@dataclass(frozen=True)
class GeneratedReport:
    """report kosong berarti tidak ada irisan kasus: hanya peringatan, tidak ada yang disimpan."""

    report: Report | None
    sessions: tuple[ReportSession, ...]
    results: tuple[ReportResult, ...]
    warning: str | None


class ReportGenerator:
    def __init__(self, db: Session, reader: SessionRecordReader) -> None:
        self._db = db
        self._reader = reader

    def generate(
        self, *, actor_id: uuid.UUID, role: Role, request: ReportRequest
    ) -> GeneratedReport:
        """Buat dan simpan satu laporan beku, atau jelaskan kenapa belum bisa.

        Hak akses diperiksa lebih dulu, sebelum data dibaca. Tanpa irisan kasus yang sama
        hasilnya hanya peringatan: tidak ada angka dan tidak ada yang disimpan (AC1).
        """
        ensure_can_create(role, request.score_scope, request.product_label_mode)
        records = load_records(self._reader, request.session_ids, request.measurement_filter)
        plan = plan_for_scope(records, request.score_scope)
        _ensure_comparable(plan, role)
        if plan.warning is not None:
            return GeneratedReport(report=None, sessions=(), results=(), warning=plan.warning)

        report, sessions, results, basis = _build(actor_id, request, records, plan)
        reports_service.save_report(self._db, report, sessions, results, basis)
        self._db.commit()
        return GeneratedReport(
            report=report, sessions=tuple(sessions), results=tuple(results), warning=None
        )


def _ensure_comparable(plan: ComparisonPlan, role: Role) -> None:
    """409 bila pilihan tidak sah, ada sesi tidak layak, atau syarat perbandingan gagal."""
    if not (plan.selection_errors or plan.ineligible or plan.rejections):
        return
    raise ConflictError(
        "Sesi yang dipilih belum bisa dibandingkan. Lihat alasannya satu per satu.",
        code=REPORT_NOT_COMPARABLE,
        details={
            "selection_errors": list(plan.selection_errors),
            "ineligible": {str(sid): list(codes) for sid, codes in plan.ineligible.items()},
            "rejections": [
                {"requirement": tolakan.requirement, "message": tolakan.message}
                for tolakan in (rejection_for_role(r, role) for r in plan.rejections)
            ],
        },
    )


def _build(
    actor_id: uuid.UUID,
    request: ReportRequest,
    records: Sequence[SessionRecord],
    plan: ComparisonPlan,
) -> tuple[Report, list[ReportSession], list[ReportResult], list[ReportCaseBasis]]:
    scope = request.score_scope
    facts = [record.facts for record in records]
    report = Report(
        id=uuid.uuid4(),
        title=request.title,
        score_scope=scope,
        has_dev_scores=scope is not ScoreScope.TEST,
        measurement_filter=request.measurement_filter,
        product_label_mode=request.product_label_mode,
        language=request.language,
        # Syarat perbandingan sudah memastikan rubrik dan model penilai sama dan diketahui.
        rubric_version_id=facts[0].rubric_version_id,
        judge_model_name=facts[0].judge_model_version,
        case_basis_count=len(plan.shared_cases),
        created_by=actor_id,
    )
    labels = {
        record.facts.session_id: product_label(
            request.product_label_mode, record.facts.product_name, index
        )
        for index, record in enumerate(records)
    }
    sessions = [_session_row(report, record, labels, plan) for record in records]
    results = _result_rows(report, records, labels, plan)
    basis = _basis_rows(report, records, plan)
    return report, sessions, results, basis


def _session_row(
    report: Report,
    record: SessionRecord,
    labels: Mapping[uuid.UUID, str],
    plan: ComparisonPlan,
) -> ReportSession:
    facts = record.facts
    codes = [
        _MARKER_CODES[marker]
        for marker in (*plan.session_markers.get(facts.session_id, ()), *plan.markers)
        if marker in _MARKER_CODES
    ]
    return ReportSession(
        report_id=report.id,
        session_id=facts.session_id,
        product_id=facts.product_id,
        product_label=labels[facts.session_id],
        measurement_kind=record.measurement_kind,
        is_trial=SessionPurpose(facts.purpose) is SessionPurpose.TRIAL,
        markers=[{"code": code.value, "detail": None} for code in codes],
    )


def _result_rows(
    report: Report,
    records: Sequence[SessionRecord],
    labels: Mapping[uuid.UUID, str],
    plan: ComparisonPlan,
) -> list[ReportResult]:
    shared = {(ref.case_id, ref.case_version_id) for ref in plan.shared_cases}
    splits = sorted(reports_service.allowed_splits(report.score_scope), key=_SPLIT_ORDER.index)
    rows: list[ReportResult] = []
    for split in splits:
        scores = {
            record.facts.session_id: score_cases(
                cases_for_split(_shared_cases(record, shared), split)
            )
            for record in records
        }
        ranks = rank_products([RankEntry(sid, labels[sid], score) for sid, score in scores.items()])
        for record in records:
            sid = record.facts.session_id
            rows.append(_result_row(report, record, split, scores[sid], ranks[sid], shared))
    return rows


def _result_row(
    report: Report,
    record: SessionRecord,
    split: ScoreSplit,
    score: SplitScore,
    rank: int | None,
    shared: set[tuple[uuid.UUID, uuid.UUID]],
) -> ReportResult:
    facts = record.facts
    return ReportResult(
        report_id=report.id,
        session_id=facts.session_id,
        score_split=split,
        cases_graded_count=len(cases_for_split(record.graded_cases, split)),
        cases_basis_count=len(cases_for_split(_shared_cases(record, shared), split)),
        det_score=round_score(score.det),
        llm_score=round_score(score.llm),
        total_score=round_score(score.total),
        failure_rate=failure_rate(record.measurement_kind, calls_for_split(record, split)),
        rank=rank,
        repetitions=facts.repetitions,
        product_model_version=facts.model_version,
        judge_model_version=facts.judge_model_version,
    )


def _shared_cases(
    record: SessionRecord, shared: set[tuple[uuid.UUID, uuid.UUID]]
) -> tuple[GradedCase, ...]:
    return tuple(
        case
        for case in record.graded_cases
        if (case.ref.case_id, case.ref.case_version_id) in shared
    )


def _basis_rows(
    report: Report, records: Sequence[SessionRecord], plan: ComparisonPlan
) -> list[ReportCaseBasis]:
    split_of: dict[tuple[uuid.UUID, uuid.UUID], SplitTag] = {}
    for record in records:
        for case in record.graded_cases:
            split_of.setdefault((case.ref.case_id, case.ref.case_version_id), case.split)
    return [
        ReportCaseBasis(
            report_id=report.id,
            case_id=ref.case_id,
            case_version_id=ref.case_version_id,
            split_tag=split_of[(ref.case_id, ref.case_version_id)],
        )
        for ref in plan.shared_cases
    ]
