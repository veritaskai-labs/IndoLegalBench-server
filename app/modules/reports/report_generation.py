"""Pembuat laporan perbandingan. PBI-15 SCRUM-79 SCRUM-313.

AC1, AC2, AC6, AC7, AC9, AC12, AC14. Mengurutkan langkahnya saja: hak akses, baca sesi,
aturan perbandingan (SCRUM-301), hitung angka, simpan beku (SCRUM-300). Aturannya sendiri
hidup di modul masing-masing.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.reports.models import (
    MeasurementFilter,
    ProductLabelMode,
    Report,
    ReportLanguage,
    ReportResult,
    ReportSession,
    ScoreScope,
)
from app.modules.reports.session_record import SessionRecordReader
from app.shared.security import Role

REPORT_SESSION_NOT_FOUND = "REPORT_SESSION_NOT_FOUND"
REPORT_NOT_COMPARABLE = "REPORT_NOT_COMPARABLE"


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
        raise NotImplementedError
