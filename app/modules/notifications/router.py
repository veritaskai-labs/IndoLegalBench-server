"""Endpoint HTTP modul notifications.

Router hanya menerjemahkan HTTP ke pemanggilan service dan sebaliknya.
Tidak ada logika bisnis di sini, dan tidak ada query database di sini.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationRead, UnreadCount
from app.shared.database import get_db
from app.shared.pagination import Page
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

_TOLAK = {
    403: {
        "model": ErrorBody,
        "description": "Notifikasi milik pengguna lain (`NOTIFICATION_NOT_YOURS`)",
    },
    404: {
        "model": ErrorBody,
        "description": "Notifikasi tidak ditemukan (`NOTIFICATION_NOT_FOUND`)",
    },
}


def _user_id(user: CurrentUser) -> uuid.UUID:
    if isinstance(user.user_id, uuid.UUID):
        return user.user_id
    return uuid.UUID(str(user.user_id))


@router.get(
    "",
    response_model=Page[NotificationRead],
    summary="Daftar notifikasi milik pengguna saat ini",
)
def list_notifications(
    unread: bool | None = Query(
        default=None,
        description="True = hanya belum dibaca. False atau kosong = semua.",
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Page[NotificationRead]:
    return service.list_notifications(
        db, _user_id(user), unread=unread, page=page, size=size
    )


@router.get(
    "/unread-count",
    response_model=UnreadCount,
    summary="Jumlah notifikasi belum dibaca",
)
def unread_count(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UnreadCount:
    return service.get_unread_count(db, _user_id(user))


@router.post(
    "/read-all",
    response_model=UnreadCount,
    status_code=status.HTTP_200_OK,
    summary="Tandai semua notifikasi sebagai dibaca",
)
def read_all(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UnreadCount:
    return service.mark_all_notifications_read(db, _user_id(user))


@router.post(
    "/{notification_id}/read",
    response_model=NotificationRead,
    status_code=status.HTTP_200_OK,
    summary="Tandai satu notifikasi sebagai dibaca",
    responses=_TOLAK,
)
def read_one(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> NotificationRead:
    return service.mark_notification_read(db, _user_id(user), notification_id)
