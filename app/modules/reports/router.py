"""Endpoint HTTP modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

SCRUM-313: POST /reports membuat laporan dan GET /reports/eligible-sessions mendaftar sesi
beserta kelayakannya. Rute statis eligible-sessions harus didaftarkan SEBELUM rute
/{report_id} (SCRUM-320), kalau tidak FastAPI menganggapnya sebuah report_id. Hal yang sama
berlaku untuk GET /reports/trend (PBI-17 SCRUM-331).

SCRUM-332 (penerbitan, diagram D1 dan D9):
- POST /reports/{id}/publish: Admin menerbitkan laporan tersimpan, atau mengganti satu versi
- GET /published-reports: daftar versi yang masih berlaku, untuk setiap peran yang login

SCRUM-320 (riwayat laporan dan PDF, diagram D3 langkah 10-15):
- GET /reports: daftar laporan tersimpan dan terbit yang boleh dilihat (D9)
- GET /reports/{id}: buka ulang satu laporan dari hasil beku
- GET /reports/{id}/pdf: unduh laporan yang sama sebagai PDF
Semua peran yang login boleh memanggil; siapa melihat laporan mana diputuskan service
(reports/access.py).
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.reports import history, publication
from app.modules.reports.dependencies import (
    get_eligible_session_lister,
    get_report_generator,
    get_trend_service,
)
from app.modules.reports.eligible_sessions import EligibleSessionLister
from app.modules.reports.models import MeasurementFilter, ScoreScope
from app.modules.reports.report_generation import ReportGenerator
from app.modules.reports.schemas import (
    EligibleSessionRead,
    PublicationRead,
    PublishedReportListItem,
    PublishReport,
    ReportCreate,
    ReportCreateResponse,
    ReportDetail,
    ReportNotComparableBody,
    ReportSessionNotFoundBody,
    ReportSummary,
    TrendRead,
)
from app.modules.reports.trend import TrendMetric, TrendRange
from app.modules.reports.trend_service import TrendService
from app.shared.database import get_db
from app.shared.pagination import Page, PageParams
from app.shared.security import CurrentUser, Role, get_current_user, require_roles

router = APIRouter(prefix="/reports", tags=["reports"])

# Semua peran boleh membuat laporan; jenis nilai dan label yang boleh dipilih dibatasi
# creation_policy di service (403 dari server, bukan dari tombol yang disembunyikan).
_create_roles = require_roles(*Role)
# Tren dibuka semua peran; skor dev dijaga ensure_can_view_trend di service.
_trend_roles = require_roles(*Role)

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


_TREND_RESPONSES = {
    403: {
        "model": ErrorBody,
        "description": "Peran tidak boleh melihat tren yang memuat skor dev "
        "(`TREND_SCOPE_FORBIDDEN`).",
    },
    404: {
        "model": ErrorBody,
        "description": "Produk AI tidak ditemukan (`TREND_PRODUCT_NOT_FOUND`).",
    },
}


@router.get(
    "/trend",
    response_model=TrendRead,
    summary="Tren skor satu produk antar bulan",
    responses=_TREND_RESPONSES,
)
def get_trend(
    product_id: uuid.UUID = Query(description="Produk AI yang trennya dilihat."),
    score_scope: ScoreScope = Query(
        default=ScoreScope.TEST,
        description="Jenis kasus: `test` (bawaan, skor resmi), `dev`, atau `dev_test` (All). "
        "`dev` dan `dev_test` hanya untuk Admin dan Developer AI.",
    ),
    metric: TrendMetric = Query(
        default=TrendMetric.TOTAL, description="Satu metrik per grafik; skor tidak digabung."
    ),
    range_: TrendRange = Query(
        default=TrendRange.ALL,
        alias="range",
        description="Bulan kalender terakhir: `3m`, `6m`, `12m`, atau `all` (bawaan).",
    ),
    user: CurrentUser = Depends(_trend_roles),
    service: TrendService = Depends(get_trend_service),
) -> TrendRead:
    """Satu titik per bulan dari sesi layak terbaru, hanya pada kasus yang sama (PBI-17)."""
    result = service.get_trend(
        role=user.role,
        product_id=product_id,
        scope=score_scope,
        metric=metric,
        window=range_,
    )
    return TrendRead.from_trend(result)


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


@router.post(
    "/{report_id}/publish",
    response_model=PublicationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Terbitkan laporan tersimpan, atau ganti satu versi yang masih berlaku",
    responses={
        403: {"model": ErrorBody, "description": "Bukan Admin"},
        404: {"model": ErrorBody, "description": "Laporan atau versi yang diganti tidak ada"},
        409: {"model": ErrorBody, "description": "Tidak bisa diterbitkan"},
    },
)
def publish_saved_report(
    report_id: uuid.UUID,
    payload: PublishReport | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(Role.ADMIN)),
) -> PublicationRead:
    """Admin boleh menerbitkan laporan orang lain. version_no diisi server."""
    return publication.publish(
        db,
        report_id=report_id,
        actor_id=user.user_id,
        supersedes_id=None if payload is None else payload.supersedes_id,
    )


published_router = APIRouter(prefix="/published-reports", tags=["reports"])


@published_router.get(
    "",
    response_model=Page[PublishedReportListItem],
    summary="Daftar laporan yang sedang terbit",
    responses={401: {"model": ErrorBody, "description": "Belum login"}},
)
def list_published_reports(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> Page[PublishedReportListItem]:
    """Versi yang sudah diganti tetap tersimpan, tetapi tidak muncul di daftar ini."""
    return publication.list_published(db, PageParams(page=page, size=size))
