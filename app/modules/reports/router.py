"""Endpoint HTTP modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

SCRUM-313: POST /reports membuat laporan dan GET /reports/eligible-sessions mendaftar sesi
beserta kelayakannya. Rute statis eligible-sessions harus didaftarkan SEBELUM rute
/{report_id} (SCRUM-320), kalau tidak FastAPI menganggapnya sebuah report_id. Penerbitan
menyusul di SCRUM-332.

SCRUM-320 (riwayat laporan dan PDF, diagram D3 langkah 10-15):
- GET /reports: daftar laporan tersimpan dan terbit yang boleh dilihat (D9)
- GET /reports/{id}: buka ulang satu laporan dari hasil beku
- GET /reports/{id}/pdf: unduh laporan yang sama sebagai PDF
Semua peran yang login boleh memanggil; siapa melihat laporan mana diputuskan service
(reports/access.py).

SCRUM-326 (penelusuran angka sampai bukti, PBI-16, diagram D6 langkah 1-7):
- GET /reports/{id}/results/{result_id}/cases: kasus dasar di balik satu angka
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.reports import drilldown, history
from app.modules.reports.dependencies import get_eligible_session_lister, get_report_generator
from app.modules.reports.eligible_sessions import EligibleSessionLister
from app.modules.reports.models import MeasurementFilter
from app.modules.reports.report_generation import ReportGenerator
from app.modules.reports.schemas import (
    EligibleSessionRead,
    ReportCreate,
    ReportCreateResponse,
    ReportDetail,
    ReportNotComparableBody,
    ReportResultCasesRead,
    ReportSessionNotFoundBody,
    ReportSummary,
)
from app.shared.database import get_db
from app.shared.pagination import Page, PageParams
from app.shared.security import CurrentUser, Role, get_current_user, require_roles

router = APIRouter(prefix="/reports", tags=["reports"])

# Semua peran boleh membuat laporan; jenis nilai dan label yang boleh dipilih dibatasi
# creation_policy di service (403 dari server, bukan dari tombol yang disembunyikan).
_create_roles = require_roles(*Role)

_CREATE_RESPONSES = {
    200: {
        "model": ReportCreateResponse,
        "description": "Tidak ada kasus yang dijawab semua produk: hanya peringatan "
        "`NO_SHARED_CASES`, tidak ada angka dan tidak ada laporan yang tersimpan.",
    },
    403: {
        "model": ErrorBody,
        "description": "Jenis nilai atau label di luar hak peran (`REPORT_SCOPE_FORBIDDEN`, "
        "`REPORT_LABEL_MODE_FORBIDDEN`).",
    },
    404: {
        "model": ReportSessionNotFoundBody,
        "description": "Ada sesi yang dipilih tidak ditemukan (`REPORT_SESSION_NOT_FOUND`).",
    },
    409: {
        "model": ReportNotComparableBody,
        "description": "Pilihan belum bisa dibandingkan (`REPORT_NOT_COMPARABLE`): pilihan tidak "
        "sah, sesi tidak layak, atau syarat rubrik, penilai, dan suite tidak terpenuhi.",
    },
}


@router.post(
    "",
    response_model=ReportCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Buat laporan perbandingan dari sesi pengukuran",
    responses=_CREATE_RESPONSES,
)
def create_report(
    payload: ReportCreate,
    response: Response,
    user: CurrentUser = Depends(_create_roles),
    generator: ReportGenerator = Depends(get_report_generator),
) -> ReportCreateResponse:
    """Hitung dan simpan satu laporan beku (PBI-15 AC1, AC2, AC6, AC7, AC9, AC14)."""
    generated = generator.generate(
        actor_id=user.user_id, role=user.role, request=payload.to_request()
    )
    if generated.report is None:
        response.status_code = status.HTTP_200_OK
    return ReportCreateResponse.from_generated(generated)


@router.get(
    "/eligible-sessions",
    response_model=list[EligibleSessionRead],
    summary="Daftar sesi beserta kelayakannya untuk laporan",
)
def list_eligible_sessions(
    measurement_filter: MeasurementFilter = Query(
        default=MeasurementFilter.ALL,
        description="Saring sesi otomatis atau manual saja; bawaannya semuanya.",
    ),
    _user: CurrentUser = Depends(_create_roles),
    lister: EligibleSessionLister = Depends(get_eligible_session_lister),
) -> list[EligibleSessionRead]:
    """Semua sesi aktif dengan eligible dan alasannya, supaya halaman tidak menunggu 409.

    eligible hanya menilai satu sesi; syarat antar sesi (rubrik, penilai, suite) tetap dicek
    saat POST /reports (PBI-15 AC2, AC4).
    """
    items = lister.list(measurement_filter=measurement_filter)
    return [EligibleSessionRead.from_item(item) for item in items]


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
    return history.list_reports(db, user, PageParams(page=page, size=size))


# Rute /{report_id} di bawah ini sengaja didaftarkan setelah /eligible-sessions (lihat docstring).
_TIDAK_ADA = {
    404: {
        "model": ErrorBody,
        "description": "Laporan tidak ada, atau tidak boleh dibuka peran ini (`REPORT_NOT_FOUND`)",
    }
}


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
    return history.get_report(db, report_id, user)


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
    isi, nama_berkas = history.report_pdf(db, report_id, user)
    return Response(
        content=isi,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nama_berkas}"'},
    )


@router.get(
    "/{report_id}/results/{result_id}/cases",
    response_model=ReportResultCasesRead,
    summary="Kasus dasar di balik satu angka laporan, tanpa isi kasus",
    responses={
        404: {
            "model": ErrorBody,
            "description": "Laporan tidak boleh dibuka atau tidak ada (`REPORT_NOT_FOUND`), "
            "angka bukan milik laporan ini (`REPORT_RESULT_NOT_FOUND`), atau hasil penilaiannya "
            "tidak ditemukan (`REPORT_GRADES_NOT_FOUND`)",
        }
    },
)
def list_result_cases(
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ReportResultCasesRead:
    """PBI-16 AC1: kode kasus, versi kasus, dan skor per kasus untuk semua peran (D9)."""
    return drilldown.list_cases(db, report_id, result_id, user)
