"""Riwayat laporan tersimpan dan unduhan PDF (PBI-15 SCRUM-320, diagram D3 langkah 10-15).

Membaca hasil beku dari tabel D1 apa adanya: membuka ulang laporan dan membuat PDF tidak
pernah menghitung ulang (AC10). Siapa melihat laporan mana diputuskan reports/access.py
(D9), dan satu aturan yang sama dipakai daftar, detail, dan PDF.

Dipisah dari service.py (aturan simpan SCRUM-300) karena pembuat laporan SCRUM-313 memakai
service.py lewat session_record, sedangkan modul ini memakai schemas dan pdf; kalau
disatukan, impornya melingkar.
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.reports import repository
from app.modules.reports.access import sees_product_mapping, visibility_for, visible_case_code
from app.modules.reports.models import ProductLabelMode, Report, ReportResult
from app.modules.reports.pdf import ReportPdfRenderer, report_pdf_filename
from app.modules.reports.repository import ReportContents
from app.modules.reports.schemas import (
    ProductLabelMappingRead,
    ReportCaseBasisRead,
    ReportDetail,
    ReportDetailResultRead,
    ReportDetailSessionRead,
    ReportMarkerRead,
    ReportSummary,
)
from app.shared.exceptions import NotFoundError
from app.shared.pagination import Page, PageParams
from app.shared.security import CurrentUser

REPORT_NOT_FOUND = "REPORT_NOT_FOUND"

_PDF = ReportPdfRenderer()


def list_reports(db: Session, user: CurrentUser, params: PageParams) -> Page[ReportSummary]:
    """Daftar laporan tersimpan yang boleh dilihat pengguna (AC riwayat, D3 langkah 10)."""
    rows, total = repository.list_reports(
        db, visibility_for(user), offset=params.offset, limit=params.size
    )
    labels = repository.frozen_product_labels(db, [row.id for row in rows])
    return Page(
        items=[ReportSummary.from_report(row, labels.get(row.id, [])) for row in rows],
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
    report = readable_report(db, report_id, user)
    contents = repository.contents(db, report)
    detail = _detail(report, contents, user)
    if not sees_product_mapping(user.role, report.product_label_mode):
        return detail
    names = repository.product_names(db, [session.product_id for session in contents.sessions])
    mapping = [
        ProductLabelMappingRead(
            label=session.product_label, real_name=names.get(session.product_id)
        )
        for session in contents.sessions
    ]
    return detail.model_copy(update={"product_mapping": mapping})


def readable_report(db: Session, report_id: uuid.UUID, user: CurrentUser) -> Report:
    """Laporan yang boleh dibaca pengguna ini, atau 404 (D9).

    Satu pintu untuk riwayat, PDF, dan penelusuran angka (PBI-16), supaya aturan siapa
    membuka laporan mana tidak pernah berbeda di antara ketiganya.
    """
    report = repository.get_report(db, report_id)
    if report is None or not visibility_for(user).allows(report):
        raise NotFoundError("Laporan tidak ditemukan.", code=REPORT_NOT_FOUND)
    return report


def report_pdf(db: Session, report_id: uuid.UUID, user: CurrentUser) -> tuple[bytes, str]:
    """PDF dan nama berkasnya, dari ReportDetail yang sama dengan halaman web (D3 langkah 13-15).

    Aturan baca ikut get_report (readable_report), jadi laporan yang tidak boleh dibuka juga
    tidak bisa diunduh. Bahasa PDF mengikuti reports.language (disiapkan untuk PBI-26).
    """
    report = readable_report(db, report_id, user)
    # PDF dibagikan ke luar, jadi dibuat tanpa pemetaan kode ke nama asli walau pembacanya Admin.
    detail = _detail(report, repository.contents(db, report), user)
    return _PDF.render(detail), report_pdf_filename(detail)


def _detail(report: Report, contents: ReportContents, user: CurrentUser) -> ReportDetail:
    # AC9: di mode kode, siapa pun pembacanya, laporan tidak boleh membuka produk di balik
    # kode. PDF yang diunduh Admin pun dibagikan ke luar, jadi aturannya tidak per peran.
    anonim = report.product_label_mode is ProductLabelMode.CODE
    sessions = [
        ReportDetailSessionRead(
            product_label=session.product_label,
            measurement_kind=session.measurement_kind,
            is_trial=session.is_trial,
            markers=[ReportMarkerRead.model_validate(raw) for raw in session.markers],
            results=[
                _result(result, anonim=anonim)
                for result in contents.results
                if result.session_id == session.session_id
            ],
        )
        for session in contents.sessions
    ]
    return ReportDetail.model_validate(
        {
            **ReportSummary.from_report(
                report, [session.product_label for session in contents.sessions]
            ).model_dump(),
            "measurement_filter": report.measurement_filter,
            "product_label_mode": report.product_label_mode,
            "rubric_version_no": contents.rubric_version_no,
            "judge_model_name": report.judge_model_name,
            "case_basis_count": report.case_basis_count,
            "sessions": sessions,
            "case_basis": [
                ReportCaseBasisRead(
                    case_code=visible_case_code(user.role, row.case_code, row.split_tag),
                    case_version_no=row.case_version_no,
                    split_tag=row.split_tag,
                )
                for row in contents.case_basis
            ],
        }
    )


def _result(result: ReportResult, *, anonim: bool) -> ReportDetailResultRead:
    read = ReportDetailResultRead.model_validate(result)
    # Versi model produk biasanya menyebut nama produknya (mis. "Justisio-v3"), jadi ikut
    # ditutup di mode kode (review #145). Versi model juri tetap tampil (AC3).
    return read.model_copy(update={"product_model_version": None}) if anonim else read
