"""Kontrol Admin; Dev AI hanya miliknya bila flag diaktifkan. SCRUM-77 SCRUM-216."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.runs.models import SessionStatus
from app.modules.runs.schemas import (
    SessionControlResponse,
    SessionResumeRequest,
    SessionResumeResponse,
)
from app.modules.runs.session_control import resume_session, stop_session
from app.shared.config import Settings, get_settings
from app.shared.database import get_db
from app.shared.exceptions import ForbiddenError
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter()
_roles = require_roles(Role.ADMIN, Role.DEVELOPER_AI)


def _controller(user: CurrentUser = Depends(_roles), settings: Settings = Depends(get_settings)):
    if user.role == Role.DEVELOPER_AI and not settings.measurement_dev_ai_controls_enabled:
        raise ForbiddenError("Kontrol Developer AI belum diaktifkan.")
    return user


def _now():
    return datetime.now(UTC)


_RESPONSES = {
    401: {"model": ErrorBody, "description": "Belum masuk (`UNAUTHENTICATED`)."},
    403: {
        "model": ErrorBody,
        "description": "Hanya Admin, atau Dev AI pemilik bila flag aktif (`FORBIDDEN`, `NOT_YOUR_SESSION`, `ADMIN_CONFIRMATION_REQUIRED`).",
    },
    404: {"model": ErrorBody, "description": "Sesi tidak ada (`MEASUREMENT_NOT_FOUND`)."},
    409: {
        "model": ErrorBody,
        "description": (
            "Transisi tidak sah atau berulang (`INVALID_TRANSITION`), sesi diarsipkan "
            "(`SESSION_ARCHIVED`), diterima apa adanya (`SESSION_ACCEPTED`), atau manual "
            "(`SESSION_NOT_AUTOMATIC`)."
        ),
    },
}


@router.post(
    "/{session_id}/pause",
    status_code=202,
    response_model=SessionControlResponse,
    responses=_RESPONSES,
    summary="Jeda seluruh sesi; call aktif tetap disimpan sebelum worker berhenti",
)
def pause(
    session_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(_controller)
) -> SessionControlResponse:
    return stop_session(db, session_id, target=SessionStatus.PAUSED, now=_now(), actor=user)


@router.post(
    "/{session_id}/cancel",
    status_code=202,
    response_model=SessionControlResponse,
    responses=_RESPONSES,
    summary="Batalkan seluruh sesi; hasil, call log dan biaya tetap tersimpan",
)
def cancel(
    session_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(_controller)
) -> SessionControlResponse:
    return stop_session(db, session_id, target=SessionStatus.CANCELLED, now=_now(), actor=user)


@router.post(
    "/{session_id}/resume",
    status_code=202,
    response_model=SessionResumeResponse,
    responses={
        **_RESPONSES,
        409: {
            "model": ErrorBody,
            "description": "Resume ditolak: MODEL_VERSION_CHANGED (mulai sesi baru), MODEL_VERSION_CONFIRMATION_REQUIRED (konfirmasi Admin), ATTEMPT_RECOVERY_REQUIRED, ANOTHER_SESSION_RUNNING, SESSION_NOT_RESUMABLE, SESSION_ARCHIVED, SESSION_ACCEPTED, SESSION_NOT_AUTOMATIC.",
        },
    },
    summary="Lanjutkan sisa pekerjaan dengan guard versi dan penanda jeda panjang",
)
def resume(
    session_id: uuid.UUID,
    payload: SessionResumeRequest = SessionResumeRequest(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_controller),
    settings: Settings = Depends(get_settings),
) -> SessionResumeResponse:
    return resume_session(
        db,
        session_id,
        actor=user,
        now=_now(),
        long_stop_days=settings.resume_long_stop_days,
        confirm_unverified_version=payload.confirm_unverified_version,
    )
