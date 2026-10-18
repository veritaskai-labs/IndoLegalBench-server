"""Logika bisnis modul notifications.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain.
DbNotifier mengimplementasikan port Notifier di reviews.ports: sesi
pendek sendiri, commit sendiri, dan tidak pernah melempar ke pemanggil.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.modules.notifications import repository
from app.modules.notifications.models import Notification
from app.modules.notifications.schemas import NotificationRead, UnreadCount
from app.shared.database import get_session_factory
from app.shared.exceptions import ForbiddenError, NotFoundError
from app.shared.pagination import Page

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

NOT_YOURS = "NOTIFICATION_NOT_YOURS"
NOT_FOUND = "NOTIFICATION_NOT_FOUND"


def list_notifications(
    db: Session,
    user_id: uuid.UUID,
    *,
    unread: bool | None,
    page: int,
    size: int,
) -> Page[NotificationRead]:
    unread_only = unread is True
    offset = (page - 1) * size
    baris = repository.list_for_user(
        db, user_id, unread_only=unread_only, offset=offset, limit=size
    )
    total = repository.count_for_user(db, user_id, unread_only=unread_only)
    return Page[NotificationRead](
        items=[NotificationRead.model_validate(item) for item in baris],
        total=total,
        page=page,
        size=size,
    )


def get_unread_count(db: Session, user_id: uuid.UUID) -> UnreadCount:
    return UnreadCount(count=repository.unread_count(db, user_id))


def mark_notification_read(
    db: Session, user_id: uuid.UUID, notification_id: uuid.UUID
) -> NotificationRead:
    """Tandai satu notifikasi dibaca. Sudah dibaca → 200 idempotent.

    Notifikasi milik user lain → 403. Tidak ada → 404.
    """
    row = repository.get_by_id(db, notification_id)
    if row is None:
        raise NotFoundError("Notifikasi tidak ditemukan", code=NOT_FOUND)
    if row.user_id != user_id:
        raise ForbiddenError("Notifikasi ini milik pengguna lain", code=NOT_YOURS)
    repository.mark_read(db, row, when=datetime.now(UTC))
    db.commit()
    db.refresh(row)
    return NotificationRead.model_validate(row)


def mark_all_notifications_read(db: Session, user_id: uuid.UUID) -> UnreadCount:
    repository.mark_all_read(db, user_id, when=datetime.now(UTC))
    db.commit()
    return UnreadCount(count=0)


class DbNotifier:
    """Notifier yang menulis ke tabel notifications.

    Membuka sesi pendek sendiri (bukan sesi transaksi bisnis pemanggil),
    commit per panggilan, dan menelan error supaya penugasan/verdict
    tidak ikut gagal. Payload hanya memakai title / body / link.
    """

    def __init__(
        self, session_factory: SessionFactory | sessionmaker[Session] | None = None
    ) -> None:
        self._session_factory = session_factory

    def notify(self, user_ids: Sequence[uuid.UUID], type: str, payload: Mapping[str, str]) -> None:
        if not user_ids:
            return
        title = payload.get("title") or ""
        body = payload.get("body") or ""
        link = payload.get("link") or ""
        factory = self._session_factory or get_session_factory()
        db: Session | None = None
        try:
            db = factory()
            rows = [
                Notification(
                    user_id=user_id,
                    type=type,
                    title=title,
                    body=body,
                    link=link,
                )
                for user_id in user_ids
            ]
            repository.insert_many(db, rows)
            db.commit()
        except Exception:
            if db is not None:
                db.rollback()
            logger.exception(
                "Gagal menyimpan notifikasi type=%s untuk %d pengguna",
                type,
                len(user_ids),
            )
        finally:
            if db is not None:
                db.close()
