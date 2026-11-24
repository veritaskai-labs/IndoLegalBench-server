"""Endpoint HTTP modul reports.

PBI-15, PBI-16, PBI-17 Laporan perbandingan antar produk.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

SCRUM-313: POST /reports membuat laporan dan GET /reports/eligible-sessions mendaftar sesi
beserta kelayakannya. SCRUM-362: POST /reports/validate memeriksa pilihan sesi (dry-run) dengan
aturan yang sama, tanpa menyimpan. Rute statis eligible-sessions harus didaftarkan SEBELUM rute
/{report_id} (SCRUM-320), kalau tidak FastAPI menganggapnya sebuah report_id. Hal yang sama
berlaku untuk GET /reports/trend (PBI-17 SCRUM-331) dan GET /reports/trend/pdf (SCRUM-333).
GET /reports/trend/pdf membuat PDF dari TrendRead yang sama dengan GET /reports/trend; kedua
rute membaca parameter lewat satu dependency (TrendQuery), jadi pilihan dan validasinya sama.

SCRUM-332 (penerbitan, diagram D1 dan D9):
- POST /reports/{id}/publish: Admin menerbitkan laporan tersimpan, atau mengganti satu versi
- GET /published-reports: daftar versi yang masih berlaku, untuk setiap peran yang login

SCRUM-320 (riwayat laporan dan PDF, diagram D3 langkah 10-15):
- GET /reports: daftar laporan tersimpan dan terbit yang boleh dilihat (D9)
- GET /reports/{id}: buka ulang satu laporan dari hasil beku
- GET /reports/{id}/pdf: unduh laporan yang sama sebagai PDF
Semua peran yang login boleh memanggil; siapa melihat laporan mana diputuskan service
(reports/access.py).

SCRUM-326 (penelusuran angka sampai bukti, PBI-16, diagram D6 langkah 1-7):
- GET /reports/{id}/results/{result_id}/cases: kasus dasar di balik satu angka
- GET /reports/{id}/results/{result_id}/cases/{case_id}: jawaban asli per percobaan
- GET .../cases/{case_id}/attempts/{attempt_no}/evidence dan .../evidence/{id}/file: bukti
  jawaban manual, hanya Admin dan Kontributor (AC4). Memakai service bukti SCRUM-233; route
  sendiri karena endpoint bukti lama butuh session_id, yang ditutup di laporan mode kode.

SCRUM-327 (PBI-16 AC5 sampai AC7): GET /reports/sessions/{id}/csv mengunduh berkas skor
satu sesi. Hanya sesi layak D2; selain itu 409 dengan alasannya. GET ini sengaja menulis:
setiap berkas yang diserahkan disimpan (AC7), isi yang sama untuk sesi yang sama sekali saja.
Pathnya tiga segmen, jadi tidak bertabrakan dengan /{report_id} maupun /{report_id}/pdf.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.reports import drilldown, history, publication
from app.modules.reports.dependencies import (
    get_eligible_session_lister,
    get_now,
    get_report_generator,
    get_report_validator,
    get_score_file_service,
    get_trend_pdf_renderer,
    get_trend_service,
)
from app.modules.reports.eligible_sessions import (
    EligibleSessionLister,
    EligibleSessionQuery,
    SessionValidity,
)
from app.modules.reports.models import MeasurementFilter, ReportLanguage, ScoreScope
from app.modules.reports.report_generation import ReportGenerator
from app.modules.reports.report_validation import ReportValidator
from app.modules.reports.schemas import (
    EligibleSessionRead,
    PublicationRead,
    PublishedReportListItem,
    PublishReport,
    ReportCaseDetailRead,
    ReportCreate,
    ReportCreateResponse,
    ReportDetail,
    ReportNotComparableBody,
    ReportResultCasesRead,
    ReportSessionNotFoundBody,
    ReportSummary,
    ReportValidateRequest,
    ReportValidationRead,
    TrendRead,
)
from app.modules.reports.score_files import SessionScoreFileService
from app.modules.reports.trend import TrendMetric, TrendRange
from app.modules.reports.trend_pdf import TrendPdfRenderer, trend_pdf_filename
from app.modules.reports.trend_service import TrendService
from app.modules.runs.dependencies import get_manual_evidence_service
from app.modules.runs.manual_evidence import ManualEvidenceService
from app.modules.runs.schemas import EvidenceRead
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


@router.post(
    "/validate",
    response_model=ReportValidationRead,
    summary="Periksa pilihan sesi untuk laporan tanpa menyimpan (dry-run)",
    responses={
        403: _CREATE_RESPONSES[403],
        404: _CREATE_RESPONSES[404],
    },
)
def validate_report_selection(
    payload: ReportValidateRequest,
    user: CurrentUser = Depends(_create_roles),
    validator: ReportValidator = Depends(get_report_validator),
) -> ReportValidationRead:
    """Semua pelanggaran sekaligus saat sesi dipilih, bukan setelah klik Buat Laporan.

    Aturannya sama dengan POST /reports (PBI-15 AC1, AC4, AC5, AC13, AC14), tetapi tidak
    menyimpan apa pun dan selalu 200 untuk pilihan yang bisa dibaca. POST /reports tetap
    memeriksa ulang di server.
    """
    validation = validator.validate(
        role=user.role,
        session_ids=tuple(payload.session_ids),
        score_scope=payload.score_scope,
        measurement_filter=payload.measurement_filter,
    )
    return ReportValidationRead.from_validation(validation)


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
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Cari nama sesi, id sesi, atau nama produk; huruf besar/kecil tidak dibedakan.",
    ),
    validity: SessionValidity = Query(
        default=SessionValidity.ALL,
        description="valid: hanya sesi layak. invalid: hanya sesi belum layak. all: semuanya.",
    ),
    product: uuid.UUID | None = Query(
        default=None, description="Hanya sesi milik produk dengan id ini."
    ),
    _user: CurrentUser = Depends(_create_roles),
    lister: EligibleSessionLister = Depends(get_eligible_session_lister),
) -> list[EligibleSessionRead]:
    """Semua sesi aktif dengan eligible dan alasannya, supaya halaman tidak menunggu 409.

    eligible hanya menilai satu sesi; syarat antar sesi (rubrik, penilai, suite) tetap dicek
    saat POST /reports (PBI-15 AC2, AC4). q, validity, dan product menyaring daftar untuk
    pemilih sesi (SCRUM-361); semuanya digabung dengan AND.
    """
    query = EligibleSessionQuery(q=q, validity=validity, product_id=product)
    items = lister.list(measurement_filter=measurement_filter, query=query)
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


@dataclass(frozen=True)
class TrendQuery:
    product_id: uuid.UUID
    score_scope: ScoreScope
    metric: TrendMetric
    range_: TrendRange


def trend_query(
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
) -> TrendQuery:
    """Parameter tren, sama untuk halaman tren dan PDF-nya (D4 langkah 8: parameter sama)."""
    return TrendQuery(product_id=product_id, score_scope=score_scope, metric=metric, range_=range_)


def _trend_read(query: TrendQuery, user: CurrentUser, service: TrendService) -> TrendRead:
    result = service.get_trend(
        role=user.role,
        product_id=query.product_id,
        scope=query.score_scope,
        metric=query.metric,
        window=query.range_,
    )
    return TrendRead.from_trend(result)


@router.get(
    "/trend",
    response_model=TrendRead,
    summary="Tren skor satu produk antar bulan",
    responses=_TREND_RESPONSES,
)
def get_trend(
    query: TrendQuery = Depends(trend_query),
    user: CurrentUser = Depends(_trend_roles),
    service: TrendService = Depends(get_trend_service),
) -> TrendRead:
    """Satu titik per bulan dari sesi layak terbaru, hanya pada kasus yang sama (PBI-17)."""
    return _trend_read(query, user, service)


@router.get(
    "/trend/pdf",
    response_class=Response,
    summary="Unduh tren skor satu produk sebagai PDF",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Berkas PDF tren"},
        **_TREND_RESPONSES,
    },
)
def download_trend_pdf(
    query: TrendQuery = Depends(trend_query),
    language: ReportLanguage = Query(
        default=ReportLanguage.EN,
        description="Bahasa PDF: `en` (bawaan) atau `id`. Disiapkan untuk PBI-26.",
    ),
    user: CurrentUser = Depends(_trend_roles),
    service: TrendService = Depends(get_trend_service),
    renderer: TrendPdfRenderer = Depends(get_trend_pdf_renderer),
    now: datetime = Depends(get_now),
) -> Response:
    """Tren yang sama dengan GET /reports/trend sebagai PDF (PBI-17 AC8, D4 langkah 8).

    Aturan peran sama karena lewat TrendService: Viewer dan Kontributor yang meminta skor dev
    ditolak 403 sebelum sesi dibaca.
    """
    trend = _trend_read(query, user, service)
    isi = renderer.render(trend, language=language, generated_at=now)
    nama_berkas = trend_pdf_filename(trend, language=language, generated_at=now)
    return Response(
        content=isi,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nama_berkas}"'},
    )


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


@router.get(
    "/{report_id}/results/{result_id}/cases/{case_id}",
    response_model=ReportCaseDetailRead,
    summary="Satu kasus di balik angka laporan, sampai jawaban asli tiap percobaan",
    responses={
        403: {
            "model": ErrorBody,
            "description": "Peran ini tidak boleh membuka isi kasus (`CASE_CONTENT_FORBIDDEN`): "
            "Viewer, Kontributor untuk kasus dev, Developer AI untuk kasus test. Pesannya "
            "menyebut email untuk meminta isi kasus.",
        },
        404: {
            "model": ErrorBody,
            "description": "Laporan tidak boleh dibuka atau tidak ada (`REPORT_NOT_FOUND`), angka "
            "bukan milik laporan ini (`REPORT_RESULT_NOT_FOUND`), kasus tidak ada di balik angka "
            "ini (`REPORT_CASE_NOT_FOUND`), atau hasil penilaiannya tidak ditemukan "
            "(`REPORT_GRADES_NOT_FOUND`)",
        },
    },
)
def get_result_case(
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ReportCaseDetailRead:
    """PBI-16 AC2 dan AC3: jawaban asli, skor, alasan, versi rubrik, dan data jawaban manual.

    `other_products` memuat jawaban produk lain di laporan yang sama untuk kasus ini, dengan
    status `answered`, `not_graded` (tampilkan "tidak dinilai"), atau `no_answer`. Aturan akses
    sama dengan isi kasus, jadi peran tanpa hak mendapat 403 tanpa jawaban produk mana pun.
    """
    return drilldown.case_detail(db, report_id, result_id, case_id, user)


# PBI-16 AC4 dan D9 Sprint 4: bukti jawaban manual hanya Admin dan Kontributor.
_evidence_roles = require_roles(Role.ADMIN, Role.CONTRIBUTOR)
_EVIDENCE_ERRORS = {
    403: {
        "model": ErrorBody,
        "description": "Peran ini tidak boleh membuka bukti (`FORBIDDEN`) atau isi kasus "
        "(`CASE_CONTENT_FORBIDDEN`)",
    },
    404: {
        "model": ErrorBody,
        "description": "Laporan, angka, kasus, percobaan, atau bukti tidak ditemukan di balik "
        "angka ini (`REPORT_NOT_FOUND`, `REPORT_RESULT_NOT_FOUND`, `REPORT_CASE_NOT_FOUND`, "
        "`REPORT_EVIDENCE_NOT_FOUND`, `EVIDENCE_NOT_FOUND`)",
    },
}
_CASE_PATH = "/{report_id}/results/{result_id}/cases/{case_id}"


@router.get(
    _CASE_PATH + "/attempts/{attempt_no}/evidence",
    response_model=list[EvidenceRead],
    summary="Bukti satu jawaban manual dari penelusuran laporan (Admin, Kontributor)",
    responses=_EVIDENCE_ERRORS,
)
def list_result_case_evidence(
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    attempt_no: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_evidence_roles),
    evidence: ManualEvidenceService = Depends(get_manual_evidence_service),
) -> list[EvidenceRead]:
    return drilldown.list_evidence(db, report_id, result_id, case_id, attempt_no, user, evidence)


@router.get(
    _CASE_PATH + "/evidence/{evidence_id}/file",
    response_class=Response,
    summary="Unduh berkas bukti jawaban manual dari penelusuran laporan (Admin, Kontributor)",
    responses={
        200: {
            "description": "Isi berkas, selalu sebagai lampiran",
            "content": {"image/png": {}, "image/jpeg": {}, "application/pdf": {}},
        },
        **_EVIDENCE_ERRORS,
    },
)
def download_result_case_evidence(
    report_id: uuid.UUID,
    result_id: uuid.UUID,
    case_id: uuid.UUID,
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_evidence_roles),
    evidence: ManualEvidenceService = Depends(get_manual_evidence_service),
) -> Response:
    berkas = drilldown.evidence_file(db, report_id, result_id, case_id, evidence_id, user, evidence)
    return Response(
        content=berkas.content,
        media_type=berkas.mime_type,
        headers={
            # Sama dengan unduhan bukti di manual_router: lampiran, bukan inline.
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(berkas.file_name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


_SCORE_FILE_RESPONSES = {
    200: {"content": {"text/csv": {}}, "description": "Berkas CSV skor satu sesi"},
    404: {"model": ErrorBody, "description": "Sesi pengukuran tidak ada"},
    409: {
        "model": ErrorBody,
        "description": "Sesi belum layak diunduh (D2), atau tidak ada kasus yang boleh "
        "dilihat peran ini (`SCORE_FILE_NO_VISIBLE_CASES`). `code` alasan pertama; "
        "`reasons` berisi semua alasan dengan kode dan kalimatnya.",
    },
}


@router.get(
    "/sessions/{session_id}/csv",
    response_class=Response,
    summary="Unduh skor per kasus satu sesi sebagai CSV",
    description="Semua kasus sesi ditulis; kasus tanpa skor berstatus `tidak dinilai`. "
    "Setiap berkas yang diserahkan disimpan selamanya (AC7), jadi permintaan ini menulis "
    "ke basis data; isi yang sama untuk sesi yang sama hanya disimpan sekali.",
    responses=_SCORE_FILE_RESPONSES,
)
def download_session_score_file(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: SessionScoreFileService = Depends(get_score_file_service),
) -> Response:
    berkas = service.download(session_id, user_id=user.user_id, role=user.role)
    return Response(
        content=berkas.content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{berkas.file_name}"'},
    )
