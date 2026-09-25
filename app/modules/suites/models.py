"""Tabel database milik modul suites.

ATURAN: file ini hanya boleh diimpor oleh file lain di dalam
app/modules/suites/. Modul lain yang butuh data suite memanggil
suites.service, bukan tabel ini langsung.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Uuid, column, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


class SuiteStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Suite(Base):
    __tablename__ = "suites"
    # lower(name), bukan unique biasa, supaya "Hukum" dan "hukum" bentrok
    # di PostgreSQL maupun SQLite.
    __table_args__ = (Index("uq_suites_name_ci", func.lower(column("name", String)), unique=True),)

    # Uuid generik SQLAlchemy 2.0, jadi tabel yang sama bisa dipakai
    # PostgreSQL di production dan SQLite saat test.
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[SuiteStatus] = mapped_column(
        Enum(
            SuiteStatus,
            name="suite_status_enum",
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        default=SuiteStatus.ACTIVE,
    )
    # FK lewat nama tabel, bukan import model auth. Batas modul tetap utuh.
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
