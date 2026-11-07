"""Endpoint sesi pengukuran manual. PBI-22 (SCRUM-176), SCRUM-230.

Prefix sendiri (/manual-measurements) sesuai tiket: sesi manual tidak lewat ringkasan
biaya, anggaran, maupun antrean sesi otomatis (AC10).
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.runs.dependencies import (
    get_manual_session_service,
    get_manual_status_service,
)
from app.modules.runs.manual_answers import (
    manual_answer_history,
    save_manual_answer,
)
from app.modules.runs.manual_session import ManualSessionService
from app.modules.runs.manual_session_status import ManualSessionStatusService
from app.modules.runs.schemas import (
    ManualAnswerHistoryEntry,
    ManualAnswerResponse,
    ManualAnswerSaveRequest,
    ManualFinalizeResponse,
    ManualSessionDetail,
    ManualStartRequest,
    ManualStartResponse,
)
from app.shared.config import Settings, get_settings
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(prefix="/manual-measurements", tags=["manual-measurements"])

# AC1: Admin membuat sesi manual. AC11: semua Admin dan Kontributor melihat sesi orang lain.
_admin = require_roles(Role.ADMIN)
_viewers = require_roles(Role.ADMIN, Role.CONTRIBUTOR)


@router.post(
    "",
    response_model=ManualStartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Buat sesi manual: snapshot kasus saat itu juga, status Belum terisi",
    responses={
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
                "Isian tidak valid, produk otomatis atau nonaktif (`PRODUCT_NOT_MANUAL`, "
                "`PRODUCT_INACTIVE`), atau tidak ada kasus layak (`NO_ELIGIBLE_CASES`)."
            ),
        },
    },
)
def create_manual_session(
    payload: ManualStartRequest,
    user: CurrentUser = Depends(_admin),
    service: ManualSessionService = Depends(get_manual_session_service),
) -> ManualStartResponse:
    return service.create(payload, created_by=uuid.UUID(str(user.user_id)))


@router.post(
    "/{session_id}/finalize",
    response_model=ManualFinalizeResponse,
    summary="Tandai sesi manual selesai: final, tidak dapat dibuka kembali",
    responses={
        404: {
            "model": ErrorBody,
            "description": "Sesi manual tidak ditemukan (`MANUAL_SESSION_NOT_FOUND`).",
        },
        409: {
            "model": ErrorBody,
            "description": (
                "Masih ada jawaban kosong (`MANUAL_SESSION_INCOMPLETE`) atau sesi sudah "
                "selesai (`MANUAL_SESSION_FINALIZED`)."
            ),
        },
    },
)
def finalize_manual(
    session_id: uuid.UUID,
    # Siapa yang boleh menandai Selesai masih TBA; standar tiket: Admin (AC8).
    user: CurrentUser = Depends(_admin),
    service: ManualSessionStatusService = Depends(get_manual_status_service),
) -> ManualFinalizeResponse:
    return service.finalize(
        session_id, finalized_by=uuid.UUID(str(user.user_id)), now=datetime.now(UTC)
    )


@router.get(
    "/{session_id}",
    response_model=ManualSessionDetail,
    summary="Progres sesi manual: X dari N jawaban terisi dan kartu per kasus",
    responses={
        404: {
            "model": ErrorBody,
            "description": "Sesi manual tidak ditemukan (`MANUAL_SESSION_NOT_FOUND`).",
        },
    },
)
def read_manual_session(
    session_id: uuid.UUID,
    unfilled: bool = Query(
        default=False, description="Filter 'belum terisi': hanya kasus yang masih kosong"
    ),
    _user: CurrentUser = Depends(_viewers),
    service: ManualSessionStatusService = Depends(get_manual_status_service),
    settings: Settings = Depends(get_settings),
) -> ManualSessionDetail:
    return service.detail(
        session_id,
        unfilled_only=unfilled,
        warning_days=settings.manual_answer_date_warning_days,
    )


@router.put(
    "/{session_id}/answers",
    response_model=ManualAnswerResponse,
    summary="Simpan atau timpa jawaban manual",
    responses={
        409: {
            "model": ErrorBody,
            "description": "Konfirmasi overwrite diperlukan atau sesi sudah selesai.",
        },
        422: {
            "model": ErrorBody,
            "description": "Jawaban kosong atau percobaan tidak valid.",
        },
    },
)
def put_manual_answer(
    session_id: uuid.UUID,
    payload: ManualAnswerSaveRequest,
    user: CurrentUser = Depends(_viewers),
    db: Session = Depends(get_db),
) -> ManualAnswerResponse:
    return save_manual_answer(
        db,
        session_id,
        payload,
        actor=uuid.UUID(str(user.user_id)),
    )


@router.get(
    "/{session_id}/answers/{snapshot_item_id}/{attempt_no}/history",
    response_model=list[ManualAnswerHistoryEntry],
    summary="Riwayat perubahan jawaban manual",
)
def get_manual_answer_history(
    session_id: uuid.UUID,
    snapshot_item_id: uuid.UUID,
    attempt_no: int,
    _user: CurrentUser = Depends(_viewers),
    db: Session = Depends(get_db),
) -> list[ManualAnswerHistoryEntry]:
    return manual_answer_history(
        db,
        session_id,
        snapshot_item_id,
        attempt_no,
    )
