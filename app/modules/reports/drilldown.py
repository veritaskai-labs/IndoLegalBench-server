"""Penelusuran angka laporan sampai bukti (PBI-16 SCRUM-326, diagram D6 langkah 1-7).

Setiap angka di laporan adalah satu baris report_results (satu sesi, satu jenis skor).
Angka itu dibuka menjadi daftar kasus dasar beserta versi dan skornya (AC1), lalu satu
kasus dibuka sampai jawaban asli tiap percobaan (AC2).

Path memakai id angka (report_results.id), bukan session_id seperti tulisan D6: laporan
mode kode tidak boleh membuka sesi dan produk di balik kodenya (review #145).

Skor per kasus dibaca dari run penilaian yang dipakai laporan saat dibuat
(repository.grading_run_at), bukan run terbaru, supaya penelusuran selalu cocok dengan
angka laporan yang beku walaupun sesinya dinilai ulang (D1).
"""

import uuid
from collections.abc import Mapping
from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.cases.models import SplitTag
from app.modules.reports import history, repository
from app.modules.reports.access import visible_case_code
from app.modules.reports.models import Report, ReportResult, ScoreSplit
from app.modules.reports.repository import CaseBasisRow
from app.modules.reports.schemas import ReportCaseScoreRead, ReportResultCasesRead
from app.modules.runs.case_visibility import can_view_drilldown_content
from app.modules.runs.schemas import CaseGradeResult, SessionGradingResponse
from app.modules.runs.session_grade import DbGradingStore
from app.shared.exceptions import NotFoundError
from app.shared.security import CurrentUser

REPORT_RESULT_NOT_FOUND = "REPORT_RESULT_NOT_FOUND"
REPORT_GRADES_NOT_FOUND = "REPORT_GRADES_NOT_FOUND"

# Angka dev hanya dibentuk kasus dev, angka test dari kasus test, gabungan dari keduanya
# (D1, sama dengan service._ALLOWED_CASE_TAGS untuk jenis nilai laporan).
_CASE_TAGS: Mapping[ScoreSplit, frozenset[SplitTag]] = {
    ScoreSplit.TEST: frozenset({SplitTag.TEST}),
    ScoreSplit.DEV: frozenset({SplitTag.DEV}),
    ScoreSplit.COMBINED: frozenset(SplitTag),
}


class GradingRunReader(Protocol):
    """Satu run penilaian lengkap dengan kasus dan percobaannya (runs.session_grade)."""

    def get_grading_run(
        self, session_id: uuid.UUID, grading_run_id: uuid.UUID
    ) -> SessionGradingResponse | None: ...


def list_cases(
    db: Session,
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    user: CurrentUser,
    *,
    runs: GradingRunReader | None = None,
) -> ReportResultCasesRead:
    """Kasus dasar di balik satu angka laporan, tanpa isi kasus (AC1, D6 langkah 2-3).

    Terbuka untuk setiap peran yang boleh membaca laporannya (D9). Laporan yang tidak boleh
    dibaca dan angka dari laporan lain sama-sama dijawab 404.
    """
    report = history.readable_report(db, report_id, user)
    result = _result(db, report, result_id)
    run = _frozen_run(db, report, result, runs or DbGradingStore(db))
    graded = {case.case_id: case for case in run.cases}
    session = repository.get_report_session(db, report.id, result.session_id)
    return ReportResultCasesRead(
        report_id=report.id,
        result_id=result.id,
        product_label=session.product_label,
        measurement_kind=session.measurement_kind,
        score_split=result.score_split,
        rubric_version=run.rubric_version,
        cases=[
            _case_row(basis, graded.get(basis.case_id), user)
            for basis in repository.case_basis(db, report.id)
            if basis.split_tag in _CASE_TAGS[result.score_split]
        ],
    )


def _result(db: Session, report: Report, result_id: uuid.UUID) -> ReportResult:
    result = repository.get_result(db, report.id, result_id)
    if result is None:
        raise NotFoundError("Angka laporan tidak ditemukan.", code=REPORT_RESULT_NOT_FOUND)
    return result


def _frozen_run(
    db: Session, report: Report, result: ReportResult, runs: GradingRunReader
) -> SessionGradingResponse:
    run_id = repository.grading_run_at(db, result.session_id, report.created_at)
    run = runs.get_grading_run(result.session_id, run_id) if run_id is not None else None
    if run is None:
        # Hanya terjadi pada data rusak: SCRUM-313 tidak menyimpan laporan tanpa run selesai.
        raise NotFoundError(
            "Hasil penilaian di balik angka ini tidak ditemukan.", code=REPORT_GRADES_NOT_FOUND
        )
    return run


def _case_row(
    basis: CaseBasisRow, graded: CaseGradeResult | None, user: CurrentUser
) -> ReportCaseScoreRead:
    scores = graded.scores if graded else None
    return ReportCaseScoreRead(
        case_id=basis.case_id,
        case_code=visible_case_code(user.role, basis.case_code, basis.split_tag),
        case_version_no=basis.case_version_no,
        split_tag=basis.split_tag,
        det_score=scores.deterministic if scores else None,
        # Skor LLM laporan adalah jalur kualitas; jalur format tidak ikut (session_reader).
        llm_score=scores.quality if scores else None,
        content_visible=can_view_drilldown_content(user.role, basis.split_tag.value),
    )
