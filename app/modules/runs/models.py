"""Tabel database milik modul runs."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


class CallLog(Base):
    """Satu baris per upaya.

    attempt_id belum punya tabel induk.
    TODO(SCRUM-205): foreign key ke tabel attempt setelah tabel itu ada di staging.
    """

    __tablename__ = "call_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_idr: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    usd_idr_rate: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False)
