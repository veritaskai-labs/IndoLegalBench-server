"""Penerbitan laporan perbandingan (PBI-17, SCRUM-332).

D1 hanya menerbitkan laporan perbandingan. Tren dihitung saat diminta dan
tidak punya baris, jadi tidak ada rute terbit untuk tren. D2 tidak diulang
di sini: laporan yang tersimpan sudah lolos aturan itu saat dibuat.

Yang dijaga di service, bukan hanya oleh trigger Postgres (AC12):
- skor dev ditolak (ensure_publishable);
- hanya status saved yang bisa terbit;
- pengganti menunjuk satu baris published_reports yang laporannya masih
  published, dan version_no adalah version_no lama ditambah satu.

Satu commit mencakup baris terbit baru dan kedua perubahan status, sama
seperti urutan yang ditunggu trigger deferred: baris terbit ada dulu, baru
status published, lalu status lama jadi superseded.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.modules.reports import repository
from app.modules.reports.models import PublishedReport, Report, ReportStatus
from app.modules.reports.schemas import PublicationRead, PublishedReportListItem
from app.modules.reports.service import ensure_publishable
from app.shared.exceptions import ConflictError, NotFoundError
from app.shared.pagination import Page, PageParams

REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
REPORT_NOT_PUBLISHABLE = "REPORT_NOT_PUBLISHABLE"
PUBLICATION_NOT_FOUND = "PUBLICATION_NOT_FOUND"


def publish(
    db: Session,
    *,
    report_id: uuid.UUID,
    actor_id: uuid.UUID,
    supersedes_id: uuid.UUID | None = None,
) -> PublicationRead:
    """Terbitkan satu laporan tersimpan, atau ganti satu versi yang masih berlaku."""
    report = _saved_report(db, report_id)
    predecessor, replaced = _current_predecessor(db, supersedes_id)
    row = PublishedReport(
        report_id=report.id,
        version_no=1 if predecessor is None else predecessor.version_no + 1,
        published_by=actor_id,
        published_at=datetime.now(UTC),
        supersedes_id=None if predecessor is None else predecessor.id,
    )
    # Baris terbit harus ada sebelum status berubah: trigger BEFORE UPDATE
    # menolak published kalau baris published_reports belum ada.
    repository.add_publication(db, row)
    report.status = ReportStatus.PUBLISHED
    if replaced is not None:
        replaced.status = ReportStatus.SUPERSEDED
    db.commit()
    db.refresh(row)
    return PublicationRead.model_validate(row)


def list_published(db: Session, params: PageParams) -> Page[PublishedReportListItem]:
    """Versi yang laporannya masih published, terbaru dulu, dengan nama beku."""
    rows, total = repository.list_current_publications(db, offset=params.offset, limit=params.size)
    labels = repository.frozen_product_labels(db, [row.report.id for row in rows])
    items = [
        PublishedReportListItem(
            id=row.publication.id,
            report_id=row.publication.report_id,
            version_no=row.publication.version_no,
            title=row.report.title,
            published_at=row.publication.published_at,
            published_by=row.publication.published_by,
            product_labels=labels.get(row.report.id, []),
        )
        for row in rows
    ]
    return Page(items=items, total=total, page=params.page, size=params.size)


def _saved_report(db: Session, report_id: uuid.UUID) -> Report:
    report = repository.get_report(db, report_id)
    if report is None:
        raise NotFoundError("Laporan tidak ditemukan.", code=REPORT_NOT_FOUND)
    # Sebelum status, supaya laporan dev selalu 409 REPORT_HAS_DEV_SCORES.
    ensure_publishable(report)
    if report.status is not ReportStatus.SAVED:
        raise ConflictError(
            "Hanya laporan yang masih tersimpan yang bisa diterbitkan.",
            code=REPORT_NOT_PUBLISHABLE,
        )
    return report


def _current_predecessor(
    db: Session, supersedes_id: uuid.UUID | None
) -> tuple[PublishedReport | None, Report | None]:
    if supersedes_id is None:
        return None, None
    predecessor = repository.get_publication(db, supersedes_id)
    if predecessor is None:
        raise NotFoundError(
            "Versi terbit yang diganti tidak ditemukan.",
            code=PUBLICATION_NOT_FOUND,
        )
    replaced = repository.get_report(db, predecessor.report_id)
    if replaced is None or replaced.status is not ReportStatus.PUBLISHED:
        raise ConflictError(
            "Hanya versi yang sedang terbit yang bisa diganti.",
            code=REPORT_NOT_PUBLISHABLE,
        )
    return predecessor, replaced
