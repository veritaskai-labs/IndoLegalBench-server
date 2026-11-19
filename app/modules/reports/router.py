"""Endpoint HTTP modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

SCRUM-313: POST /reports membuat laporan dan GET /reports/eligible-sessions mendaftar sesi
beserta kelayakannya. Rute statis eligible-sessions harus didaftarkan SEBELUM rute
/{report_id} (SCRUM-320), kalau tidak FastAPI menganggapnya sebuah report_id. Riwayat, PDF, dan penerbitan menyusul di
SCRUM-320 dan SCRUM-332.
"""

from fastapi import APIRouter, Depends, Query, Response, status

from app.modules.auth.schemas import ErrorBody
from app.modules.reports.dependencies import get_eligible_session_lister, get_report_generator
from app.modules.reports.eligible_sessions import EligibleSessionLister
from app.modules.reports.models import MeasurementFilter
from app.modules.reports.report_generation import ReportGenerator
from app.modules.reports.schemas import (
    EligibleSessionRead,
    ReportCreate,
    ReportCreateResponse,
    ReportNotComparableBody,
    ReportSessionNotFoundBody,
)
from app.shared.security import CurrentUser, Role, require_roles

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
