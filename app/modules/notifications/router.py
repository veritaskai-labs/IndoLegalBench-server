"""Endpoint HTTP modul notifications."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationRead
from app.shared.database import get_db
from app.shared.pagination import Page
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


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
    return service.list_notifications(db, _user_id(user), unread=unread, page=page, size=size)
