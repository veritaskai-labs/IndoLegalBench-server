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

from app.modules.auth import service as auth_service
from app.modules.cases.models import SplitTag
from app.modules.reports import history, repository
from app.modules.reports.access import visible_case_code
from app.modules.reports.models import Report, ReportResult, ScoreSplit
from app.modules.reports.pdf import CONTACT_EMAIL
from app.modules.reports.repository import CaseBasisRow, ManualAnswerFacts
from app.modules.reports.schemas import (
    ReportAttemptRead,
    ReportCaseDetailRead,
    ReportCaseScoreRead,
    ReportResultCasesRead,
)
from app.modules.runs.case_visibility import can_view_drilldown_content
from app.modules.runs.manual_evidence import EvidenceDownload
from app.modules.runs.models import AttemptSource
from app.modules.runs.schemas import (
    AttemptGradeResult,
    CaseGradeResult,
    EvidenceRead,
    SessionGradingResponse,
)
from app.modules.runs.session_grade import DbGradingStore
from app.shared.exceptions import ForbiddenError, NotFoundError
from app.shared.security import CurrentUser, Role

REPORT_RESULT_NOT_FOUND = "REPORT_RESULT_NOT_FOUND"
REPORT_GRADES_NOT_FOUND = "REPORT_GRADES_NOT_FOUND"
REPORT_CASE_NOT_FOUND = "REPORT_CASE_NOT_FOUND"
CASE_CONTENT_FORBIDDEN = "CASE_CONTENT_FORBIDDEN"
REPORT_EVIDENCE_NOT_FOUND = "REPORT_EVIDENCE_NOT_FOUND"

# Angka dev hanya dibentuk kasus dev, angka test dari kasus test, gabungan dari keduanya
# (D1, sama dengan service._ALLOWED_CASE_TAGS untuk jenis nilai laporan).
_CASE_TAGS: Mapping[ScoreSplit, frozenset[SplitTag]] = {
    ScoreSplit.TEST: frozenset({SplitTag.TEST}),
    ScoreSplit.DEV: frozenset({SplitTag.DEV}),
    ScoreSplit.COMBINED: frozenset(SplitTag),
}


class EvidenceReader(Protocol):
    """Bukti jawaban manual yang sudah ada (runs.manual_evidence, SCRUM-233), D6 langkah 8."""

    def list(
        self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID, attempt_no: int, *, role: Role
    ) -> list[EvidenceRead]: ...

    def download(
        self, session_id: uuid.UUID, evidence_id: uuid.UUID, *, role: Role
    ) -> EvidenceDownload: ...


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


def case_detail(
    db: Session,
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    user: CurrentUser,
    *,
    runs: GradingRunReader | None = None,
) -> ReportCaseDetailRead:
    """Satu kasus dibuka sampai jawaban asli tiap percobaan (AC2, D6 langkah 5-7).

    Hak melihat isi dicek sebelum run penilaian dibaca (D6 langkah 6), jadi peran yang
    tidak berhak tidak pernah memicu pembacaan jawaban asli: Viewer, Kontributor untuk
    kasus dev, dan Developer AI untuk kasus test (AC3, D9).
    """
    report, result, basis = _openable_case(db, report_id, result_id, case_id, user)
    run = _frozen_run(db, report, result, runs or DbGradingStore(db))
    graded = next((case for case in run.cases if case.case_id == case_id), None)
    if graded is None:
        raise NotFoundError(
            "Hasil penilaian kasus ini tidak ditemukan.", code=REPORT_GRADES_NOT_FOUND
        )
    session = repository.get_report_session(db, report.id, result.session_id)
    manual = _manual_facts(db, graded.attempts)
    scores = graded.scores
    return ReportCaseDetailRead(
        report_id=report.id,
        result_id=result.id,
        case_id=case_id,
        case_code=basis.case_code,
        case_version_no=basis.case_version_no,
        split_tag=basis.split_tag,
        title=graded.title,
        category=graded.category,
        question=graded.question,
        product_label=session.product_label,
        measurement_kind=session.measurement_kind,
        rubric_version=run.rubric_version,
        det_score=scores.deterministic if scores else None,
        llm_score=scores.quality if scores else None,
        attempts=[_attempt(attempt, manual) for attempt in graded.attempts],
    )


def list_evidence(
    db: Session,
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    attempt_no: int,
    user: CurrentUser,
    evidence: EvidenceReader,
) -> list[EvidenceRead]:
    """Bukti satu jawaban manual dari penelusuran (AC4, D6 langkah 8).

    Pemeriksaannya sama dengan membuka isi kasus, lalu service bukti yang sudah ada
    menegakkan flag MANUAL_EVIDENCE_VISIBILITY. Route membatasi ke Admin dan Kontributor.
    """
    _, result, _ = _openable_case(db, report_id, result_id, case_id, user)
    item_id = repository.snapshot_item_id(db, result.session_id, case_id)
    if item_id is None:
        raise NotFoundError("Kasus tidak ada di balik angka ini.", code=REPORT_CASE_NOT_FOUND)
    return evidence.list(result.session_id, item_id, attempt_no, role=user.role)


def evidence_file(
    db: Session,
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: CurrentUser,
    evidence: EvidenceReader,
) -> EvidenceDownload:
    """Berkas satu bukti, hanya bila bukti itu milik kasus yang sedang ditelusuri (AC4).

    Tanpa cek kasus, laporan ini bisa dipakai membuka bukti kasus lain di sesi yang sama.
    """
    _, result, _ = _openable_case(db, report_id, result_id, case_id, user)
    if repository.evidence_case_id(db, result.session_id, evidence_id) != case_id:
        raise NotFoundError("Bukti tidak ada pada kasus ini.", code=REPORT_EVIDENCE_NOT_FOUND)
    return evidence.download(result.session_id, evidence_id, role=user.role)


def _openable_case(
    db: Session,
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    user: CurrentUser,
) -> tuple[Report, ReportResult, CaseBasisRow]:
    """Laporan boleh dibaca, angka milik laporan, kasus di balik angka, dan hak isinya (D6 6)."""
    report = history.readable_report(db, report_id, user)
    result = _result(db, report, result_id)
    basis = _basis_case(db, report, result, case_id)
    if not can_view_drilldown_content(user.role, basis.split_tag.value):
        raise ForbiddenError(
            "Isi kasus dan jawaban asli tidak dapat dibuka dengan peran Anda. Untuk "
            f"mendapatkan isi kasus, kirim email ke {CONTACT_EMAIL}.",
            code=CASE_CONTENT_FORBIDDEN,
        )
    return report, result, basis


def _basis_case(
    db: Session, report: Report, result: ReportResult, case_id: uuid.UUID
) -> CaseBasisRow:
    # Hanya kasus yang membentuk angka ini: kasus dev tidak bisa dibuka lewat angka test.
    allowed = _CASE_TAGS[result.score_split]
    basis = next(
        (
            row
            for row in repository.case_basis(db, report.id)
            if row.case_id == case_id and row.split_tag in allowed
        ),
        None,
    )
    if basis is None:
        raise NotFoundError("Kasus tidak ada di balik angka ini.", code=REPORT_CASE_NOT_FOUND)
    return basis


def _manual_facts(
    db: Session, attempts: list[AttemptGradeResult]
) -> dict[uuid.UUID, tuple[ManualAnswerFacts, str | None]]:
    """Pengisi (nama) dan tanggal jawaban untuk percobaan manual saja (AC2)."""
    ids = [a.attempt_id for a in attempts if a.source is AttemptSource.MANUAL]
    facts = repository.manual_answer_facts(db, ids)
    names = auth_service.user_names(db, {f.filled_by for f in facts.values() if f.filled_by})
    return {
        attempt_id: (fact, names.get(fact.filled_by) if fact.filled_by else None)
        for attempt_id, fact in facts.items()
    }


def _attempt(
    attempt: AttemptGradeResult, manual: dict[uuid.UUID, tuple[ManualAnswerFacts, str | None]]
) -> ReportAttemptRead:
    fact, filled_by_name = manual.get(attempt.attempt_id, (None, None))
    score = attempt.score
    return ReportAttemptRead(
        attempt_id=attempt.attempt_id,
        repetition_no=attempt.repetition_no,
        source=attempt.source,
        status=attempt.status,
        # Gagal teknis (jawaban kosong, platform menolak, judge error) tidak punya skor dan
        # ditampilkan "tidak dinilai", bukan skor 0 (AC2).
        graded=not attempt.is_technical_disruption and score is not None,
        det_score=score.deterministic if score else None,
        llm_score=score.quality if score else None,
        answer_text=attempt.answer_text,
        explanation=attempt.explanation,
        reasons=attempt.reasons,
        criteria=attempt.criteria,
        filled_by_name=filled_by_name,
        answered_at=fact.answered_at if fact else None,
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
