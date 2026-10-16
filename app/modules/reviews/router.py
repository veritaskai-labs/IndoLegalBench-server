"""Endpoint HTTP modul reviews.

PBI-6, SCRUM-147: Penggantian reviewer dan antrean penugasan ulang (AC7, AC8).
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.reviews import service
from app.modules.reviews.schemas import (
    NeedsReassignmentResponse,
    ReplaceReviewerRequest,
    ReplaceReviewerResponse,
)
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(prefix="/reviews", tags=["reviews"])

_can_manage = require_roles(Role.ADMIN)


def _user_id(user: CurrentUser) -> uuid.UUID:
    """Normalize session user id to UUID."""
    if isinstance(user.user_id, uuid.UUID):
        return user.user_id
    return uuid.UUID(str(user.user_id))


@router.get(
    "/needs-reassignment",
    response_model=list[NeedsReassignmentResponse],
    summary="Daftar kasus yang memerlukan penugasan reviewer ulang (AC8)",
    dependencies=[Depends(_can_manage)],
    responses={
        401: {"model": ErrorBody, "description": "Tidak ada sesi aktif."},
        403: {"model": ErrorBody, "description": "Hanya admin yang boleh mengakses antrean ini."},
    },
)
def get_needs_reassignment(
    db: Session = Depends(get_db),
) -> list[NeedsReassignmentResponse]:
    """Mengembalikan daftar kasus dengan round aktif yang memerlukan penugasan ulang reviewer.

    Kasus yang reviewer-nya dinonaktifkan (void) otomatis muncul di antrean ini (AC8).
    """
    return service.get_needs_reassignment(db)


@router.post(
    "/{assignment_id}/replace",
    response_model=ReplaceReviewerResponse,
    status_code=status.HTTP_200_OK,
    summary="Ganti reviewer pada sebuah kasus (AC7)",
    responses={
        401: {"model": ErrorBody, "description": "Tidak ada sesi aktif."},
        403: {"model": ErrorBody, "description": "Hanya admin yang boleh mengganti reviewer."},
        404: {
            "model": ErrorBody,
            "description": "Penugasan review atau reviewer baru tidak ditemukan.",
        },
        409: {
            "model": ErrorBody,
            "description": "Penugasan sudah pernah diganti atau putaran review sudah diputuskan.",
        },
        422: {
            "model": ErrorBody,
            "description": (
                "Reviewer baru tidak boleh penulis kasus, tidak boleh reviewer lain di round itu, "
                "atau bukan akun reviewer yang aktif."
            ),
        },
    },
)
def replace_reviewer(
    assignment_id: uuid.UUID,
    payload: ReplaceReviewerRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_manage),
) -> ReplaceReviewerResponse:
    """Admin mengganti reviewer yang ditugaskan pada sebuah kasus (AC7).

    Assignment lama diubah statusnya menjadi replaced dan penilaiannya tidak dihitung.
    Reviewer baru ditugaskan dan dinotifikasi.
    """
    old_assignment, new_assignment = service.replace_reviewer(
        db,
        assignment_id=assignment_id,
        new_reviewer_id=payload.new_reviewer_id,
        reason=payload.reason,
        admin_id=_user_id(user),
    )
    return ReplaceReviewerResponse(
        id=new_assignment.id,
        review_round_id=new_assignment.review_round_id,
        reviewer_id=new_assignment.reviewer_id,
        source=new_assignment.source,
        assigned_by=new_assignment.assigned_by,
        assigned_at=new_assignment.assigned_at,
        status=new_assignment.status,
        replaced_assignment_id=old_assignment.id,
    )
