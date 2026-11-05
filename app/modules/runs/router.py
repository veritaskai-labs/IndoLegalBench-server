"""Endpoint HTTP modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.

Prefix /measurements mengikuti tiket SCRUM-206 (POST /measurements/summary).
TODO(PBI-13): penilaian sesi menyusul.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.runs.dependencies import (
    get_session_detail_service,
    get_session_list_service,
    get_start_service,
    get_summary_service,
)
from app.modules.runs.schemas import (
    SessionAcceptRequest,
    SessionAcceptResponse,
    SessionArchiveResponse,
    SessionDetail,
    SessionListItem,
    StartRequest,
    StartResponse,
    SummaryRequest,
    SummaryResponse,
)
from app.modules.runs.service import StartSessionService, SummaryService
from app.modules.runs.session_accept import accept_session, clear_acceptance
from app.modules.runs.session_archive import archive_session
from app.modules.runs.session_control_router import router as control_router
from app.modules.runs.session_detail import SessionDetailService
from app.modules.runs.session_list import SessionListService
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

# Admin dan Developer AI boleh membuat pengukuran (PBI-11 user story).
_summary_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# AC10: peran yang sama memantau sesi di halaman Eksekusi.
_session_list_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# Peran yang melihat daftar boleh merapikannya. Developer AI hanya sesi miliknya
# (D10 kontrol sesi), dicek di session_archive.
_archive_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)
# PBI-12 AC9: hanya Admin yang menerima sesi apa adanya atau menghapus tandanya.
_accept_roles = require_roles(Role.ADMIN)

router = APIRouter(prefix="/measurements", tags=["measurements"])
router.include_router(control_router)

_RESPONS_RINGKASAN = {
    404: {
        "model": ErrorBody,
        "description": "Suite atau produk AI tidak ditemukan (`AI_PRODUCT_NOT_FOUND`).",
    },
    409: {
        "model": ErrorBody,
        "description": "Salah satu suite diarsipkan dan tidak bisa dipilih (`SUITE_ARCHIVED`).",
    },
    422: {
        "model": ErrorBody,
        "description": (
            "Isian tidak valid, produk manual atau nonaktif (`PRODUCT_NOT_AUTOMATIC`, "
            "`PRODUCT_INACTIVE`), tidak ada kasus layak (`NO_ELIGIBLE_CASES`), atau "
            "perkiraan biaya melebihi sisa anggaran (`BUDGET_EXCEEDED`, berisi "
            "`product_name` dan `remaining_budget_idr`)."
        ),
    },
    500: {
        "model": ErrorBody,
        "description": (
            "Konfigurasi server belum lengkap (`PRICE_NOT_CONFIGURED`, "
            "`EXCHANGE_RATE_NOT_CONFIGURED`, `AVG_ANSWER_TOKENS_NOT_CONFIGURED`)."
        ),
    },
}


@router.post(
    "/summary",
    response_model=SummaryResponse,
    summary="Ringkasan dan perkiraan biaya sebelum pengukuran dimulai",
    dependencies=[Depends(_summary_roles)],
    responses=_RESPONS_RINGKASAN,
)
def summarize(
    payload: SummaryRequest,
    service: SummaryService = Depends(get_summary_service),
) -> SummaryResponse:
    return service.summarize(payload)


@router.get(
    "",
    response_model=list[SessionListItem],
    summary="Daftar sesi pengukuran yang belum diarsipkan, terbaru di atas",
    dependencies=[Depends(_session_list_roles)],
)
def list_sessions(
    service: SessionListService = Depends(get_session_list_service),
) -> list[SessionListItem]:
    return service.list()


@router.get(
    "/{session_id}",
    response_model=SessionDetail,
    summary="Detail sesi dengan jawaban mentah per kasus dan percobaan",
    responses={
        404: {"model": ErrorBody, "description": "Sesi tidak ditemukan (`MEASUREMENT_NOT_FOUND`)."}
    },
)
def get_session(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(_session_list_roles),
    service: SessionDetailService = Depends(get_session_detail_service),
) -> SessionDetail:
    """Developer AI mendapat kasus test yang disamarkan (masked = true)."""
    return service.get(session_id, viewer_role=user.role)


_RESPONS_ARSIP = {
    403: {
        "model": ErrorBody,
        "description": (
            "Peran tidak berwenang (`FORBIDDEN`), atau Developer AI mengarsipkan sesi "
            "buatan orang lain (`NOT_YOUR_SESSION`)."
        ),
    },
    404: {"model": ErrorBody, "description": "Sesi tidak ditemukan (`MEASUREMENT_NOT_FOUND`)."},
    409: {
        "model": ErrorBody,
        "description": (
            "Sesi masih antre atau berjalan (`SESSION_NOT_ARCHIVABLE`), atau sudah "
            "diarsipkan (`SESSION_ALREADY_ARCHIVED`)."
        ),
    },
}


@router.patch(
    "/{session_id}/archive",
    response_model=SessionArchiveResponse,
    summary="Arsipkan sesi yang sudah berakhir supaya tidak tampil di daftar",
    responses=_RESPONS_ARSIP,
)
def archive(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(_archive_roles),
    db: Session = Depends(get_db),
) -> SessionArchiveResponse:
    return archive_session(
        db,
        session_id,
        archived_by=uuid.UUID(str(user.user_id)),
        actor_role=user.role,
        now=datetime.now(UTC),
    )


_RESPONS_TERIMA = {
    403: {"model": ErrorBody, "description": "Hanya Admin (`FORBIDDEN`)."},
    404: {"model": ErrorBody, "description": "Sesi tidak ditemukan (`MEASUREMENT_NOT_FOUND`)."},
}


@router.patch(
    "/{session_id}/accept",
    response_model=SessionAcceptResponse,
    summary="Terima apa adanya: sesi dengan kegagalan dianggap selesai sepenuhnya",
    responses={
        **_RESPONS_TERIMA,
        409: {
            "model": ErrorBody,
            "description": (
                "Sesi bukan Selesai dengan kegagalan (`SESSION_NOT_ACCEPTABLE`), atau sudah "
                "diterima (`ALREADY_ACCEPTED`)."
            ),
        },
        422: {"model": ErrorBody, "description": "Alasan kosong (`REASON_REQUIRED`)."},
    },
)
def accept(
    session_id: uuid.UUID,
    payload: SessionAcceptRequest,
    user: CurrentUser = Depends(_accept_roles),
    db: Session = Depends(get_db),
) -> SessionAcceptResponse:
    return accept_session(
        db,
        session_id,
        reason=payload.reason,
        accepted_by=uuid.UUID(str(user.user_id)),
        now=datetime.now(UTC),
    )


@router.delete(
    "/{session_id}/accept",
    response_model=SessionAcceptResponse,
    summary="Hapus tanda terima apa adanya; sesi bisa dicoba ulang lagi",
    responses={
        **_RESPONS_TERIMA,
        409: {"model": ErrorBody, "description": "Sesi belum diterima (`NOT_ACCEPTED`)."},
    },
    dependencies=[Depends(_accept_roles)],
)
def clear_accept(session_id: uuid.UUID, db: Session = Depends(get_db)) -> SessionAcceptResponse:
    return clear_acceptance(db, session_id)


# Tanpa endpoint duplikasi sesi (PBI-11 AC1): setiap sesi dibuat lewat Mulai.
@router.post(
    "",
    response_model=StartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mulai pengukuran: validasi ulang lalu sesi masuk antrean",
    responses=_RESPONS_RINGKASAN,
)
def start(
    payload: StartRequest,
    user: CurrentUser = Depends(_summary_roles),
    service: StartSessionService = Depends(get_start_service),
) -> StartResponse:
    return service.start(payload, created_by=uuid.UUID(str(user.user_id)))
