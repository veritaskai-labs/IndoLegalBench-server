"""Endpoint HTTP modul suites.

Router hanya menerjemahkan HTTP ke pemanggilan service dan sebaliknya.
Tidak ada logika bisnis di sini, dan tidak ada query database di sini.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.suites import service
from app.modules.suites.models import SuiteStatus
from app.modules.suites.schemas import (
    SnapshotRead,
    SnapshotSummary,
    SuiteCreate,
    SuiteRead,
    SuiteUpdate,
)
from app.shared.database import get_db
from app.shared.pagination import Page
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(prefix="/suites", tags=["suites"])
snapshot_router = APIRouter(tags=["snapshots"])

_boleh_mengelola = require_roles(Role.AUTHOR, Role.ADMIN)
_admin_only = require_roles(Role.ADMIN)
# SCRUM-138: Viewer sees snapshots. Reviewer can read them too.
_boleh_melihat_snapshot = require_roles(Role.AUTHOR, Role.REVIEWER, Role.ADMIN, Role.VIEWER)

_KONFLIK = {
    409: {
        "model": ErrorBody,
        "description": (
            "Nama sudah dipakai (`SUITE_NAME_TAKEN`), suite berisi kasus "
            "approved (`SUITE_HAS_APPROVED_CASES`), atau suite arsip dihapus."
        ),
    }
}


def _user_id(user: CurrentUser) -> uuid.UUID:
    if isinstance(user.user_id, uuid.UUID):
        return user.user_id
    return uuid.UUID(str(user.user_id))


@router.post(
    "",
    response_model=SuiteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buat suite baru",
    responses=_KONFLIK,
)
def create_suite(
    payload: SuiteCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_boleh_mengelola),
) -> SuiteRead:
    return service.create_suite(db, payload, created_by=_user_id(user))


@router.get("", response_model=Page[SuiteRead], summary="Daftar suite")
def list_suites(
    status_filter: SuiteStatus = Query(default=SuiteStatus.ACTIVE, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_mengelola),
) -> Page[SuiteRead]:
    return service.list_suites(db, status=status_filter, page=page, size=size)


@router.get("/{suite_id}", response_model=SuiteRead, summary="Detail satu suite")
def get_suite(
    suite_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_mengelola),
) -> SuiteRead:
    return service.get_suite(db, suite_id)


@router.patch(
    "/{suite_id}",
    response_model=SuiteRead,
    summary="Ubah suite",
    responses=_KONFLIK,
)
def update_suite(
    suite_id: uuid.UUID,
    payload: SuiteUpdate,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_mengelola),
) -> SuiteRead:
    return service.update_suite(db, suite_id, payload)


@router.delete(
    "/{suite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus suite, ditolak kalau berisi kasus approved",
    responses=_KONFLIK,
)
def delete_suite(
    suite_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_mengelola),
) -> None:
    service.delete_suite(db, suite_id)


@router.post(
    "/{suite_id}/archive",
    response_model=SuiteRead,
    summary="Arsipkan suite",
)
def archive_suite(
    suite_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_mengelola),
) -> SuiteRead:
    return service.archive_suite(db, suite_id)


@router.post(
    "/{suite_id}/unarchive",
    response_model=SuiteRead,
    summary="Aktifkan kembali suite yang diarsipkan",
)
def unarchive_suite(
    suite_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_mengelola),
) -> SuiteRead:
    return service.unarchive_suite(db, suite_id)


@router.post(
    "/{suite_id}/snapshots",
    response_model=SnapshotRead,
    status_code=status.HTTP_201_CREATED,
    summary="Bekukan suite sebagai snapshot",
    responses={
        403: {"model": ErrorBody, "description": "Bukan admin."},
        404: {"model": ErrorBody, "description": "Suite tidak ditemukan."},
        422: {
            "model": ErrorBody,
            "description": "Suite tidak punya kasus approved (`NOTHING_TO_SNAPSHOT`).",
        },
    },
)
def create_snapshot(
    suite_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_admin_only),
) -> SnapshotRead:
    """Freeze the approved cases. No request body and no name."""
    return service.create_snapshot(db, suite_id, created_by=_user_id(user))


@router.get(
    "/{suite_id}/snapshots",
    response_model=Page[SnapshotSummary],
    summary="Daftar snapshot satu suite",
)
def list_snapshots(
    suite_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_melihat_snapshot),
) -> Page[SnapshotSummary]:
    """List snapshots, newest first.

    Author, Reviewer, Admin, and Viewer. SCRUM-138 shows this list to Viewer.
    Creating a snapshot stays Admin only.
    """
    return service.list_snapshots(db, suite_id, page=page, size=size)


@snapshot_router.get(
    "/snapshots/{snapshot_id}",
    response_model=SnapshotRead,
    summary="Isi satu snapshot",
    responses={404: {"model": ErrorBody, "description": "Snapshot tidak ditemukan."}},
)
def get_snapshot(
    snapshot_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_boleh_melihat_snapshot),
) -> SnapshotRead:
    """Return the frozen items. The body is the stored copy.

    Author, Reviewer, Admin, and Viewer. SCRUM-138 shows this page to Viewer.
    Creating a snapshot stays Admin only.
    """
    return service.get_snapshot(db, snapshot_id)
