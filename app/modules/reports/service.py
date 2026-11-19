"""Logika bisnis modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

SCRUM-300 menyediakan aturan yang dipakai bersama pembuat laporan (SCRUM-313), riwayat
(SCRUM-320), dan penerbitan (SCRUM-332): jenis skor yang boleh ada per jenis nilai, penjaga
"laporan dengan skor dev tidak boleh terbit", dan penyimpanan laporan beku. Tidak ada
fungsi ubah atau hapus laporan (AC10): riwayat permanen.

Aturan yang sama dijaga trigger PostgreSQL (migrasi b6f3a2d81c47). Service memeriksanya
lebih dulu supaya pemanggil mendapat kode galat yang jelas, bukan IntegrityError, dan
supaya tes SQLite (tanpa trigger) ikut menjaganya.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.modules.cases.models import SplitTag
from app.modules.reports import repository
from app.modules.reports.access import visibility_for
from app.modules.reports.models import (
    MeasurementKind,
    Report,
    ReportCaseBasis,
    ReportResult,
    ReportSession,
    ScoreScope,
    ScoreSplit,
)
from app.modules.reports.repository import ReportContents
from app.modules.reports.schemas import (
    ReportCaseBasisRead,
    ReportDetail,
    ReportMarker,
    ReportResultRead,
    ReportSessionRead,
    ReportSummary,
)
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.pagination import Page, PageParams
from app.shared.security import CurrentUser

REPORT_HAS_DEV_SCORES = "REPORT_HAS_DEV_SCORES"
REPORT_SPLIT_NOT_ALLOWED = "REPORT_SPLIT_NOT_ALLOWED"
REPORT_MANUAL_FAILURE_RATE = "REPORT_MANUAL_FAILURE_RATE"
REPORT_CASE_BASIS_MISMATCH = "REPORT_CASE_BASIS_MISMATCH"
REPORT_RESULT_SESSION_UNKNOWN = "REPORT_RESULT_SESSION_UNKNOWN"
REPORT_ROW_OTHER_REPORT = "REPORT_ROW_OTHER_REPORT"
REPORT_NOT_FOUND = "REPORT_NOT_FOUND"

_ALLOWED_SPLITS: dict[ScoreScope, frozenset[ScoreSplit]] = {
    ScoreScope.TEST: frozenset({ScoreSplit.TEST}),
    ScoreScope.DEV: frozenset({ScoreSplit.DEV}),
    ScoreScope.DEV_TEST: frozenset(ScoreSplit),
}

# Kasus dev hanya boleh menjadi dasar laporan yang memang menampilkan skor dev.
_ALLOWED_CASE_TAGS: dict[ScoreScope, frozenset[SplitTag]] = {
    ScoreScope.TEST: frozenset({SplitTag.TEST}),
    ScoreScope.DEV: frozenset({SplitTag.DEV}),
    ScoreScope.DEV_TEST: frozenset(SplitTag),
}


def allowed_splits(scope: ScoreScope) -> frozenset[ScoreSplit]:
    """Baris report_results yang boleh ada untuk satu jenis nilai (D1, AC7, AC14)."""
    return _ALLOWED_SPLITS[scope]


def ensure_publishable(report: Report) -> None:
    """Tolak penerbitan laporan yang memuat skor dev (AC7, AC14, PBI-17 AC9).

    Tertutup secara bawaan: penanda dev ATAU jenis nilai selain Test sudah cukup untuk
    menolak, jadi objek yang tidak konsisten pun tidak lolos.
    """
    if report.has_dev_scores or report.score_scope != ScoreScope.TEST:
        raise ConflictError(
            "Laporan yang memuat skor dev tidak bisa diterbitkan. "
            "Buat laporan dengan nilai Test Cases untuk diterbitkan.",
            code=REPORT_HAS_DEV_SCORES,
        )


def save_report(
    db: Session,
    report: Report,
    sessions: Sequence[ReportSession],
    results: Sequence[ReportResult],
    case_basis: Sequence[ReportCaseBasis],
) -> Report:
    """Simpan satu laporan beku beserta seluruh isinya dalam satu flush.

    Pemanggil (SCRUM-313) yang commit. Semua aturan diperiksa sebelum ada baris yang
    ditulis, jadi tidak pernah ada setengah laporan.
    """
    _check_rows_belong_to(report, sessions, results, case_basis)
    _check_splits(report, results, case_basis)
    _check_failure_rates(sessions, results)
    if len(case_basis) != report.case_basis_count:
        raise ValidationError(
            f"Jumlah kasus dasar {report.case_basis_count} tidak sama dengan "
            f"{len(case_basis)} kasus yang disimpan.",
            code=REPORT_CASE_BASIS_MISMATCH,
        )
    repository.add_report(db, report, sessions, results, case_basis)
    return report


def report_titles(db: Session, report_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Judul laporan untuk kolom Objek audit log (PBI-18 F-06)."""
    if not report_ids:
        return {}
    return repository.titles(db, report_ids)


def list_reports(db: Session, user: CurrentUser, params: PageParams) -> Page[ReportSummary]:
    """Daftar laporan tersimpan yang boleh dilihat pengguna (AC riwayat, D3 langkah 10)."""
    rows, total = repository.list_reports(
        db, visibility_for(user), offset=params.offset, limit=params.size
    )
    return Page(
        items=[ReportSummary.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        size=params.size,
    )


def get_report(db: Session, report_id: uuid.UUID, user: CurrentUser) -> ReportDetail:
    """Buka ulang satu laporan dari hasil beku, tanpa menghitung ulang (D3 langkah 11).

    Laporan yang tidak boleh dibaca dijawab sama dengan laporan yang tidak ada (404),
    supaya keberadaan laporan tersimpan orang lain tidak bocor, dan isinya tidak pernah
    dibaca dari database.
    """
    report = repository.get_report(db, report_id)
    if report is None or not visibility_for(user).allows(report):
        raise NotFoundError("Laporan tidak ditemukan.", code=REPORT_NOT_FOUND)
    return _detail(report, repository.contents(db, report))


def _detail(report: Report, contents: ReportContents) -> ReportDetail:
    sessions = [
        ReportSessionRead(
            session_id=session.session_id,
            product_id=session.product_id,
            product_label=session.product_label,
            measurement_kind=session.measurement_kind,
            is_trial=session.is_trial,
            markers=[_marker(raw) for raw in session.markers],
            results=[
                ReportResultRead.model_validate(result)
                for result in contents.results
                if result.session_id == session.session_id
            ],
        )
        for session in contents.sessions
    ]
    return ReportDetail.model_validate(
        {
            **ReportSummary.model_validate(report).model_dump(),
            "measurement_filter": report.measurement_filter,
            "product_label_mode": report.product_label_mode,
            "rubric_version_no": contents.rubric_version_no,
            "judge_model_name": report.judge_model_name,
            "case_basis_count": report.case_basis_count,
            "sessions": sessions,
            "case_basis": [
                ReportCaseBasisRead.model_validate(row, from_attributes=True)
                for row in contents.case_basis
            ],
        }
    )


def _marker(raw: dict | str) -> ReportMarker:
    # Bentuk resmi {"code", "detail"} (SCRUM-300); string polos tetap dibaca sebagai kode.
    return ReportMarker(code=raw) if isinstance(raw, str) else ReportMarker.model_validate(raw)


def _check_rows_belong_to(
    report: Report,
    sessions: Sequence[ReportSession],
    results: Sequence[ReportResult],
    case_basis: Sequence[ReportCaseBasis],
) -> None:
    rows: list[ReportSession | ReportResult | ReportCaseBasis] = [
        *sessions,
        *results,
        *case_basis,
    ]
    if any(row.report_id != report.id for row in rows):
        raise ValidationError(
            "Ada baris isi laporan yang menunjuk laporan lain.", code=REPORT_ROW_OTHER_REPORT
        )
    known = {session.session_id for session in sessions}
    if any(result.session_id not in known for result in results):
        raise ValidationError(
            "Ada hasil untuk sesi yang tidak termasuk laporan ini.",
            code=REPORT_RESULT_SESSION_UNKNOWN,
        )


def _check_splits(
    report: Report, results: Sequence[ReportResult], case_basis: Sequence[ReportCaseBasis]
) -> None:
    splits = allowed_splits(report.score_scope)
    tags = _ALLOWED_CASE_TAGS[report.score_scope]
    if any(result.score_split not in splits for result in results) or any(
        basis.split_tag not in tags for basis in case_basis
    ):
        raise ValidationError(
            f"Jenis nilai {report.score_scope.value} tidak boleh memuat skor atau kasus "
            "dari jenis lain.",
            code=REPORT_SPLIT_NOT_ALLOWED,
        )


def _check_failure_rates(
    sessions: Sequence[ReportSession], results: Sequence[ReportResult]
) -> None:
    manual = {s.session_id for s in sessions if s.measurement_kind == MeasurementKind.MANUAL}
    if any(r.session_id in manual and r.failure_rate is not None for r in results):
        raise ValidationError(
            "Tingkat kegagalan tidak berlaku untuk produk manual.",
            code=REPORT_MANUAL_FAILURE_RATE,
        )
