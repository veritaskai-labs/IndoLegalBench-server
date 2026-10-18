"""Akses database modul notifications.

ATURAN: hanya notifications/service.py yang boleh memanggil file ini.
"""

from sqlalchemy.orm import Session

from app.modules.notifications.models import Notification


def insert_many(db: Session, rows: list[Notification]) -> None:
    """Tambah baris tanpa commit — pemanggil yang memegang sesi."""
    db.add_all(rows)
    db.flush()
