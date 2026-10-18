"""Logika bisnis modul notifications.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain.
DbNotifier mengimplementasikan port Notifier di reviews.ports: sesi
pendek sendiri, commit sendiri, dan tidak pernah melempar ke pemanggil.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.modules.notifications import repository
from app.modules.notifications.models import Notification
from app.shared.database import get_session_factory

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


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
