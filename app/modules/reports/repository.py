"""Akses database modul reports.

ATURAN: hanya reports/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis. Sengaja hanya ada fungsi tambah dan baca:
laporan beku tidak punya jalur ubah atau hapus (SCRUM-300, AC10).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

# Kode dan nomor versi kasus serta nomor rubrik dibaca lewat join, bukan disalin ke tabel
# laporan (keputusan review SCRUM-300). Pola impor model lintas modul ini sudah dipakai runs.
from app.modules.cases.models import CaseVersion, SplitTag
from app.modules.grading.models import GradingRun, GradingRunStatus, RubricVersion
from app.modules.reports.access import ReportVisibility
from app.modules.reports.models import (
    Report,
    ReportCaseBasis,
    ReportResult,
    ReportSession,
    ReportStatus,
)


@dataclass(frozen=True)
class CaseBasisRow:
    case_code: str
    case_version_no: int
    split_tag: SplitTag
    # Penghubung ke hasil penilaian sesi saat penelusuran angka (PBI-16 SCRUM-326).
    case_id: uuid.UUID


@dataclass(frozen=True)
class ReportContents:
    """Isi beku satu laporan, dibaca apa adanya untuk dibuka ulang dan PDF (SCRUM-320)."""

    rubric_version_no: int | None
    sessions: list[ReportSession]
    results: list[ReportResult]
    case_basis: list[CaseBasisRow]


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


def get_report(db: Session, report_id: uuid.UUID) -> Report | None:
    return db.get(Report, report_id)


def list_reports(
    db: Session, visibility: ReportVisibility, *, offset: int, limit: int
) -> tuple[list[Report], int]:
    """Laporan yang boleh dilihat satu pengguna, terbaru dulu, beserta total semua halaman."""
    query = sa.select(Report)
    if not visibility.all_saved:
        query = query.where(
            sa.or_(Report.created_by == visibility.viewer_id, Report.status != ReportStatus.SAVED)
        )
    if not visibility.include_dev:
        query = query.where(Report.has_dev_scores.is_(False))
    total = db.scalar(sa.select(sa.func.count()).select_from(query.subquery())) or 0
    ordered = query.order_by(Report.created_at.desc(), Report.id).offset(offset).limit(limit)
    return list(db.scalars(ordered)), total


def contents(db: Session, report: Report) -> ReportContents:
    rubric_version_no = db.scalar(
        sa.select(RubricVersion.version_no).where(RubricVersion.id == report.rubric_version_id)
    )
    sessions = db.scalars(
        sa.select(ReportSession)
        .where(ReportSession.report_id == report.id)
        .order_by(ReportSession.product_label)
    )
    results = db.scalars(sa.select(ReportResult).where(ReportResult.report_id == report.id))
    return ReportContents(
        rubric_version_no=rubric_version_no,
        sessions=list(sessions),
        results=list(results),
        case_basis=case_basis(db, report.id),
    )


def case_basis(db: Session, report_id: uuid.UUID) -> list[CaseBasisRow]:
    """Kasus dasar laporan beserta kode dan versinya, urut kode kasus."""
    rows = db.execute(
        # Tag dari case_versions, bukan salinan di report_case_basis: penyamaran kasus test untuk
        # Developer AI (access.visible_case_code) tidak boleh percaya salinan yang bisa salah
        # (review #146). Versi kasus approved tidak pernah diubah, jadi hasilnya tetap beku.
        sa.select(
            CaseVersion.case_code,
            CaseVersion.version_no,
            CaseVersion.split_tag,
            ReportCaseBasis.case_id,
        )
        .select_from(ReportCaseBasis)
        .join(CaseVersion, CaseVersion.id == ReportCaseBasis.case_version_id)
        .where(ReportCaseBasis.report_id == report_id)
        .order_by(CaseVersion.case_code)
    )
    return [CaseBasisRow(*row) for row in rows]


# --- Penelusuran angka laporan (PBI-16 SCRUM-326, D6 langkah 2-7) --------------


def get_result(db: Session, report_id: uuid.UUID, result_id: uuid.UUID) -> ReportResult | None:
    """Satu angka laporan. id hasil dari laporan lain dijawab None, bukan baris orang lain."""
    return db.scalar(
        sa.select(ReportResult).where(
            ReportResult.id == result_id, ReportResult.report_id == report_id
        )
    )


def get_report_session(
    db: Session, report_id: uuid.UUID, session_id: uuid.UUID
) -> ReportSession | None:
    return db.get(ReportSession, (report_id, session_id))


def grading_run_at(db: Session, session_id: uuid.UUID, at: datetime) -> uuid.UUID | None:
    """Run penilaian yang dipakai laporan yang dibuat pada waktu `at`.

    SCRUM-313 mengambil skor dari run terbaru yang selesai (is_latest), dan is_latest pindah
    ke run baru tepat saat run itu selesai (worker dan nilai ulang). Jadi run yang dipakai
    laporan adalah run selesai terakhir dengan finished_at <= waktu laporan dibuat. Dengan
    begitu skor per kasus di penelusuran tetap sama dengan angka laporan yang beku (D1),
    walaupun sesinya dinilai ulang setelah laporan dibuat.
    """
    return db.scalar(
        sa.select(GradingRun.id)
        .where(
            GradingRun.session_id == session_id,
            GradingRun.status == GradingRunStatus.FINISHED,
            GradingRun.finished_at <= at,
        )
        .order_by(
            GradingRun.finished_at.desc(), GradingRun.triggered_at.desc(), GradingRun.id.desc()
        )
        .limit(1)
    )
