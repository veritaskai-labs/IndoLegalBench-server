"""Endpoint HTTP modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

SCRUM-320 (riwayat laporan dan PDF, diagram D3 langkah 10-15):
- GET /reports: daftar laporan tersimpan dan terbit yang boleh dilihat (D9)
- GET /reports/{id}: buka ulang satu laporan dari hasil beku
- GET /reports/{id}/pdf: unduh laporan yang sama sebagai PDF

Semua peran yang login boleh memanggil; siapa melihat laporan mana diputuskan
service (reports/access.py). Endpoint membuat dan menerbitkan laporan ada di
SCRUM-313 dan SCRUM-332. Rute tetap seperti /eligible-sessions harus didaftarkan
sebelum /{report_id}.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.reports import service
from app.modules.reports.schemas import ReportDetail, ReportSummary
from app.shared.database import get_db
from app.shared.pagination import Page, PageParams
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])

_TIDAK_ADA = {
    404: {
        "model": ErrorBody,
        "description": "Laporan tidak ada, atau tidak boleh dibuka peran ini (`REPORT_NOT_FOUND`)",
    }
}


@router.get(
    "",
    response_model=Page[ReportSummary],
    summary="Daftar laporan tersimpan dan terbit yang boleh dilihat",
)
def list_reports(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Page[ReportSummary]:
    return service.list_reports(db, user, PageParams(page=page, size=size))


@router.get(
    "/{report_id}",
    response_model=ReportDetail,
    summary="Buka ulang satu laporan dari hasil beku",
    responses=_TIDAK_ADA,
)
def get_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ReportDetail:
    return service.get_report(db, report_id, user)


@router.get(
    "/{report_id}/pdf",
    response_class=Response,
    summary="Unduh laporan sebagai PDF",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Berkas PDF laporan"},
        **_TIDAK_ADA,
    },
)
def download_report_pdf(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    isi, nama_berkas = service.report_pdf(db, report_id, user)
    return Response(
        content=isi,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nama_berkas}"'},
    )
