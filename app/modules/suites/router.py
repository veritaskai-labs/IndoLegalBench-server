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
from app.modules.suites.schemas import SuiteCreate, SuiteRead, SuiteUpdate
from app.shared.database import get_db
from app.shared.pagination import Page
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(prefix="/suites", tags=["suites"])

_boleh_mengelola = require_roles(Role.AUTHOR, Role.ADMIN)

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
