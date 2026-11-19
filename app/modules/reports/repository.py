"""Akses database modul reports.

ATURAN: hanya reports/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis. Sengaja hanya ada fungsi tambah dan baca:
laporan beku tidak punya jalur ubah atau hapus (SCRUM-300, AC10).
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.reports.models import Report, ReportCaseBasis, ReportResult, ReportSession


def add_report(
    db: Session,
    report: Report,
    sessions: Sequence[ReportSession],
    results: Sequence[ReportResult],
    case_basis: Sequence[ReportCaseBasis],
) -> None:
    """Tambah laporan dan isinya. Satu flush = satu transaksi bersama laporan (trigger PG)."""
    db.add(report)
    # Laporan dulu: baris anak menunjuk reports.id dan sesinya lewat FK.
    db.flush()
    db.add_all(sessions)
    db.flush()
    db.add_all([*results, *case_basis])
    db.flush()


def titles(db: Session, report_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    query = sa.select(Report.id, Report.title).where(Report.id.in_(report_ids))
    return dict(db.execute(query).tuples().all())
