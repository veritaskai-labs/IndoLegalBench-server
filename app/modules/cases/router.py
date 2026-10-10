"""Endpoint HTTP modul cases.

PBI-3, SCRUM-106. Router hanya menerjemahkan HTTP ke pemanggilan
service. Tidak ada logika bisnis dan tidak ada query database di sini.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.cases import service
from app.modules.cases.models import CaseStatus, SplitTag
from app.modules.cases.schemas import (
    CaseCompleteness,
    CaseRead,
    CaseSummary,
    CaseWrite,
    VersionCompare,
    VersionSummary,
)
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(tags=["cases"])

_can_write = require_roles(Role.AUTHOR, Role.ADMIN)
# SCRUM-138: Viewer sees history, and a Reviewer re-reviews a new version.
_can_read_versions = require_roles(Role.AUTHOR, Role.REVIEWER, Role.ADMIN, Role.VIEWER)

_ERROR_CODES = {
    403: {"model": ErrorBody, "description": "Bukan pembuat kasus dan bukan admin."},
    404: {"model": ErrorBody, "description": "Kasus atau suite tidak ditemukan."},
    409: {
        "model": ErrorBody,
        "description": (
            "Kode kasus sudah dipakai (`CASE_CODE_TAKEN`), kode diubah setelah "
            "ada versi yang disetujui (`CASE_CODE_LOCKED`), versi yang disetujui "
            "atau sedang ditinjau diubah (`VERSION_LOCKED`), versi baru diminta "
            "saat draf atau tinjauan masih berjalan (`VERSION_IN_PROGRESS`), "
            "atau versi baru diminta sebelum ada versi yang disetujui "
            "(`NO_APPROVED_VERSION`)."
        ),
    },
    422: {
        "model": ErrorBody,
        "description": (
            "Suite tidak aktif (`SUITE_NOT_ACTIVE`), tag kosong "
            "(`SPLIT_TAG_REQUIRED`), pola case_code ditolak "
            "(`CASE_CODE_INVALID`), atau field wajib kosong (`field`)."
        ),
    },
}


def _user_id(user: CurrentUser) -> uuid.UUID:
    """Normalize the session user id to UUID. The dependency may hand back a string."""
    if isinstance(user.user_id, uuid.UUID):
        return user.user_id
    return uuid.UUID(str(user.user_id))


@router.post(
    "/suites/{suite_id}/cases",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buat kasus hukum dalam status draft",
    responses=_ERROR_CODES,
)
def create_case(
    suite_id: uuid.UUID,
    payload: CaseWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Create a draft case. Authors and admins only."""
    return service.create_case(db, suite_id, payload, actor_id=_user_id(user))


@router.get(
    "/suites/{suite_id}/cases",
    response_model=list[CaseSummary],
    summary="Daftar ringkas kasus di dalam satu suite",
    responses={404: _ERROR_CODES[404]},
)
def list_cases(
    suite_id: uuid.UUID,
    status_filter: CaseStatus | None = Query(default=None, alias="status"),
    split_tag: SplitTag | None = Query(default=None),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_write),
) -> list[CaseSummary]:
    """List the short case summary for one suite."""
    return service.list_cases(db, suite_id, status=status_filter, split_tag=split_tag)


@router.get(
    "/cases/{case_id}",
    response_model=CaseRead,
    summary="Detail satu kasus",
    responses={404: _ERROR_CODES[404]},
)
def get_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Return the full case."""
    return service.get_case(db, case_id)


@router.put(
    "/cases/{case_id}",
    response_model=CaseRead,
    summary="Ubah kasus",
    responses=_ERROR_CODES,
)
def update_case(
    case_id: uuid.UUID,
    payload: CaseWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Update the open version. The service rejects an approved version."""
    return service.update_case(
        db,
        case_id,
        payload,
        actor_id=_user_id(user),
        is_admin=user.role == Role.ADMIN,
    )


@router.post(
    "/cases/{case_id}/versions",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buat versi draf dari kasus yang sudah disetujui",
    responses=_ERROR_CODES,
)
def start_case_version(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Copy the approved wording into a new draft. No request body."""
    return service.start_new_version(
        db,
        case_id,
        actor_id=_user_id(user),
        is_admin=user.role == Role.ADMIN,
    )


@router.get(
    "/cases/{case_id}/versions",
    response_model=list[VersionSummary],
    summary="Riwayat versi satu kasus",
    responses={
        404: {"model": ErrorBody, "description": "Kasus tidak ditemukan."},
    },
)
def list_case_versions(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_read_versions),
) -> list[VersionSummary]:
    """List every stored version, oldest number first.

    Author, Reviewer, Admin, and Viewer. SCRUM-138 shows this history to
    Viewer, and a Reviewer needs it while a new version is in review.
    """
    return service.list_case_versions(db, case_id)


@router.get(
    "/cases/{case_id}/versions/compare",
    response_model=VersionCompare,
    summary="Bandingkan dua nomor versi",
    responses={
        404: {"model": ErrorBody, "description": "Kasus atau nomor versi tidak ditemukan."},
    },
)
def compare_case_versions(
    case_id: uuid.UUID,
    a: int = Query(..., ge=1, description="Nomor versi pertama"),
    b: int = Query(..., ge=1, description="Nomor versi kedua"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_read_versions),
) -> VersionCompare:
    """Diff two version numbers of one case. The diff is computed on request.

    Author, Reviewer, Admin, and Viewer. SCRUM-138 shows this compare to
    Viewer, and a Reviewer needs it while a new version is in review.
    """
    return service.compare_case_versions(db, case_id, a, b)


@router.get(
    "/cases/{case_id}/completeness",
    response_model=CaseCompleteness,
    summary="Indikator kelengkapan satu kasus",
    responses={404: _ERROR_CODES[404]},
)
def get_completeness(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_write),
) -> CaseCompleteness:
    """Return what the case still needs before it can be sent for review."""
    return service.get_completeness(db, case_id)
