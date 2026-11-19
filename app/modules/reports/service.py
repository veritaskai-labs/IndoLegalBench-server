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
from app.modules.reports.models import (
    MeasurementKind,
    Report,
    ReportCaseBasis,
    ReportMarkerCode,
    ReportResult,
    ReportSession,
    ScoreScope,
    ScoreSplit,
)
from app.shared.exceptions import ConflictError, ValidationError

REPORT_HAS_DEV_SCORES = "REPORT_HAS_DEV_SCORES"
REPORT_SPLIT_NOT_ALLOWED = "REPORT_SPLIT_NOT_ALLOWED"
REPORT_MANUAL_FAILURE_RATE = "REPORT_MANUAL_FAILURE_RATE"
REPORT_CASE_BASIS_MISMATCH = "REPORT_CASE_BASIS_MISMATCH"
REPORT_RESULT_SESSION_UNKNOWN = "REPORT_RESULT_SESSION_UNKNOWN"
REPORT_ROW_OTHER_REPORT = "REPORT_ROW_OTHER_REPORT"
REPORT_RESULTS_INCOMPLETE = "REPORT_RESULTS_INCOMPLETE"
REPORT_MARKER_INVALID = "REPORT_MARKER_INVALID"

_MARKER_KEYS = frozenset({"code", "detail"})
_MARKER_CODES = frozenset(code.value for code in ReportMarkerCode)

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
    ditulis, jadi tidak pernah ada setengah laporan. Laporan tanpa id langsung diberi id
    di sini; baris anak harus menunjuk report.id, jadi buat laporannya dengan id atau
    ambil report.id sebelum membuat baris anak. split_tag dasar kasus dicocokkan dengan
    versi kasusnya oleh trigger PostgreSQL (report_rows_check_insert).
    """
    if report.id is None:
        report.id = uuid.uuid4()
    _check_rows_belong_to(report, sessions, results, case_basis)
    _check_splits(report, results, case_basis)
    _check_results_complete(report, sessions, results)
    _check_failure_rates(sessions, results)
    _check_markers(sessions)
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


def _check_results_complete(
    report: Report, sessions: Sequence[ReportSession], results: Sequence[ReportResult]
) -> None:
    """Setiap sesi punya tepat himpunan baris skor jenis nilainya (D1): tidak kurang."""
    expected = allowed_splits(report.score_scope)
    for session in sessions:
        splits = {r.score_split for r in results if r.session_id == session.session_id}
        if splits != expected:
            raise ValidationError(
                f"Sesi {session.session_id} harus punya baris skor "
                f"{sorted(s.value for s in expected)}.",
                code=REPORT_RESULTS_INCOMPLETE,
            )


def _check_markers(sessions: Sequence[ReportSession]) -> None:
    """markers = list objek {code, detail}; bentuk keputusan SA untuk SCRUM-301 dan 313."""
    for session in sessions:
        markers = session.markers
        if not isinstance(markers, list) or not all(_is_marker(item) for item in markers):
            raise ValidationError(
                "Penanda sesi harus berupa daftar {code, detail} dengan kode yang dikenal.",
                code=REPORT_MARKER_INVALID,
            )


def _is_marker(item: object) -> bool:
    return (
        isinstance(item, dict)
        and set(item) == _MARKER_KEYS
        and item["code"] in _MARKER_CODES
        and (item["detail"] is None or isinstance(item["detail"], str))
    )
