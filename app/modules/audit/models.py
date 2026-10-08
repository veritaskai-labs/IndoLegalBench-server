"""Tabel database milik modul audit.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/audit/.

PBI-18. Bentuk tabel mengikuti diagram D5b (ERD Audit Log). Tabel ini
append-only: baris yang sudah ditulis tidak pernah diubah, dan hanya
job retensi yang boleh menghapus catatan lewat dari masa simpan.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    column,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")

# SQLite only auto-increments an INTEGER PRIMARY KEY, not a BIGINT one.
_BIGINT_PK = BigInteger().with_variant(Integer(), "sqlite")


class AuditEntityType(StrEnum):
    """Jenis objek yang dicatat, sesuai katalog event D6a."""

    CASE = "case"
    CASE_VERSION = "case_version"
    REVIEW = "review"
    SUITE = "suite"
    AI_PRODUCT = "ai_product"
    USER = "user"


class AuditLog(Base):
    """Satu kejadian penting: siapa, kapan, aksi apa, terhadap objek apa."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_occurred_at", column("occurred_at").desc()),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_logs_case_occurred_at", "case_id", "occurred_at"),
    )

    # bigint identity, bukan uuid: urutan id sama dengan urutan tulis.
    id: Mapped[int] = mapped_column(_BIGINT_PK, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Null berarti kejadian oleh sistem. FK lewat nama tabel, bukan import
    # model auth. Users tidak pernah dihapus, jadi rujukannya tetap utuh.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # Salinan peran saat kejadian. Peran pengguna bisa berubah belakangan.
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # Diisi untuk event yang terkait kasus. Dipakai membatasi Reviewer
    # hanya melihat kasus yang pernah ditugaskan kepadanya (AC5).
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    before: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mengikat beberapa baris yang lahir dari satu request.
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
