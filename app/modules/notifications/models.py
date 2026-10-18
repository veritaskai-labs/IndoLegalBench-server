"""Tabel database milik modul notifications.

ATURAN: file ini hanya boleh diimpor oleh file lain di dalam
app/modules/notifications/.
"""

import uuid

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


class Notification(Base):
    """Stub. Kolom lengkap ditambah saat implementasi hijau."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
