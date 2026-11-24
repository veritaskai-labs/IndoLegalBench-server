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
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
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

# Nama constraint di Postgres, dan teks unik SQLite. FK atau NOT NULL tidak masuk
# daftar ini, supaya kerusakan data lain tidak tersamar jadi 409 (pola grading_queue).
_TAKEN = (
    "uq_published_reports_report",
    "uq_published_reports_supersedes",
    "UNIQUE constraint failed: published_reports.report_id",
    "UNIQUE constraint failed: published_reports.supersedes_id",
)


@dataclass(frozen=True)
class _ReplacedVersion:
    """Versi yang diganti dan laporannya. Keduanya ada, atau tidak ada penggantian."""

    publication: PublishedReport
    report: Report


def publish(
    db: Session,
    *,
    report_id: uuid.UUID,
    actor_id: uuid.UUID,
    supersedes_id: uuid.UUID | None = None,
) -> PublicationRead:
    """Terbitkan satu laporan tersimpan, atau ganti satu versi yang masih berlaku."""
    report = _saved_report(db, report_id)
    replaced = _replaced_version(db, supersedes_id)
    row = PublishedReport(
        report_id=report.id,
        version_no=1 if replaced is None else replaced.publication.version_no + 1,
        published_by=actor_id,
        published_at=datetime.now(UTC),
        supersedes_id=None if replaced is None else replaced.publication.id,
    )
    try:
        # Baris terbit harus ada sebelum status berubah: trigger BEFORE UPDATE
        # menolak published kalau baris published_reports belum ada.
        repository.add_publication(db, row)
        report.status = ReportStatus.PUBLISHED
        if replaced is not None:
            replaced.report.status = ReportStatus.SUPERSEDED
        # Nilai response sudah ada di memori. Dibaca sebelum commit, karena commit
        # mengakhiri objeknya dan refresh hanya akan mengulang SELECT yang sama.
        hasil = PublicationRead.model_validate(row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if not _publication_taken(exc):
            raise
        raise ConflictError(
            "Versi ini sudah terbit atau sudah diganti.",
            code=REPORT_NOT_PUBLISHABLE,
        ) from None
    return hasil


def list_published(db: Session, params: PageParams) -> Page[PublishedReportListItem]:
    """Versi yang laporannya masih published, terbaru dulu, dengan nama beku."""
    rows, total = repository.list_current_publications(db, offset=params.offset, limit=params.size)
    labels = repository.frozen_product_labels(db, [row.report.id for row in rows])
    items = [
        PublishedReportListItem.from_current(
            row.publication, row.report, labels.get(row.report.id, [])
        )
        for row in rows
    ]
    return Page(items=items, total=total, page=params.page, size=params.size)


def _saved_report(db: Session, report_id: uuid.UUID) -> Report:
    report = repository.lock_report(db, report_id)
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


def _replaced_version(db: Session, supersedes_id: uuid.UUID | None) -> _ReplacedVersion | None:
    if supersedes_id is None:
        return None
    predecessor = repository.lock_publication(db, supersedes_id)
    if predecessor is None:
        raise NotFoundError(
            "Versi terbit yang diganti tidak ditemukan.",
            code=PUBLICATION_NOT_FOUND,
        )
    replaced = repository.lock_report(db, predecessor.report_id)
    if replaced is None or replaced.status is not ReportStatus.PUBLISHED:
        raise ConflictError(
            "Hanya versi yang sedang terbit yang bisa diganti.",
            code=REPORT_NOT_PUBLISHABLE,
        )
    return _ReplacedVersion(publication=predecessor, report=replaced)


def _publication_taken(exc: IntegrityError) -> bool:
    message = str(exc.orig)
    return any(token in message for token in _TAKEN)
