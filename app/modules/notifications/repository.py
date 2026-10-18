"""Akses database modul notifications.

ATURAN: hanya notifications/service.py yang boleh memanggil file ini.
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.notifications.models import Notification


def insert_many(db: Session, rows: list[Notification]) -> None:
    """Tambah baris tanpa commit — pemanggil yang memegang sesi."""
    db.add_all(rows)
    db.flush()


def list_for_user(
    db: Session,
    user_id: uuid.UUID,
    *,
    unread_only: bool,
    offset: int,
    limit: int,
) -> list[Notification]:
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    return (
        query.order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_for_user(db: Session, user_id: uuid.UUID, *, unread_only: bool) -> int:
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    return query.count()
