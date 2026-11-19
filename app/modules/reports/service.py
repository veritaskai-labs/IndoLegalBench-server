"""Logika bisnis modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

SCRUM-300 menyediakan aturan yang dipakai bersama pembuat laporan (SCRUM-313), riwayat
(SCRUM-320), dan penerbitan (SCRUM-332): jenis skor yang boleh ada per jenis nilai, penjaga
"laporan dengan skor dev tidak boleh terbit", dan penyimpanan laporan beku. Tidak ada
fungsi ubah atau hapus laporan (AC10): riwayat permanen.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.modules.reports.models import (
    Report,
    ReportCaseBasis,
    ReportResult,
    ReportSession,
    ScoreScope,
    ScoreSplit,
)

REPORT_HAS_DEV_SCORES = "REPORT_HAS_DEV_SCORES"
REPORT_SPLIT_NOT_ALLOWED = "REPORT_SPLIT_NOT_ALLOWED"
REPORT_MANUAL_FAILURE_RATE = "REPORT_MANUAL_FAILURE_RATE"
REPORT_CASE_BASIS_MISMATCH = "REPORT_CASE_BASIS_MISMATCH"
REPORT_RESULT_SESSION_UNKNOWN = "REPORT_RESULT_SESSION_UNKNOWN"
REPORT_ROW_OTHER_REPORT = "REPORT_ROW_OTHER_REPORT"


def allowed_splits(scope: ScoreScope) -> frozenset[ScoreSplit]:
    """Baris report_results yang boleh ada untuk satu jenis nilai (D1, AC7, AC14)."""
    raise NotImplementedError


def ensure_publishable(report: Report) -> None:
    """Tolak penerbitan laporan yang memuat skor dev (AC7, AC14, PBI-17 AC9)."""
    raise NotImplementedError


def save_report(
    db: Session,
    report: Report,
    sessions: Sequence[ReportSession],
    results: Sequence[ReportResult],
    case_basis: Sequence[ReportCaseBasis],
) -> Report:
    """Simpan satu laporan beku beserta seluruh isinya dalam satu flush."""
    raise NotImplementedError


def report_titles(db: Session, report_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Judul laporan untuk kolom Objek audit log (PBI-18 F-06)."""
    raise NotImplementedError
