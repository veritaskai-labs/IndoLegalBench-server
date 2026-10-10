"""Tabel database milik modul providers.

ATURAN: file ini hanya boleh diimpor dari dalam app/modules/providers/.

Semua model wajib mewarisi Base dari app.shared.database supaya
terdeteksi Alembic.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


class ProviderType(StrEnum):
    """ProviderType:
    - openai_compatible: OpenAI chat completions (DeepSeek, GPT). Bearer auth. The stored URL is posted as-is.
    - gemini_interactions: Gemini Interactions API. Header x-goog-api-key.
    - anthropic_messages: Anthropic Messages API. Headers x-api-key and anthropic-version.
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI_INTERACTIONS = "gemini_interactions"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class LastTestStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class ConnectionTestErrorCategory(StrEnum):
    """Kategori kegagalan uji koneksi yang mudah dipahami Admin.

    Nilai ini disimpan di last_test_error_category dan dikembalikan di
    ConnectionTestRead agar frontend tidak perlu mem-parsing pesan mentah.
    """

    ACCESS_DENIED = "access_denied"
    MODEL_NOT_FOUND = "model_not_found"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


def _enum_values(members):
    return [member.value for member in members]


class AiProduct(Base):
    """Produk AI yang akan diukur, plus kredensial yang tidak boleh dibaca ulang."""

    __tablename__ = "ai_products"
    __table_args__ = (
        CheckConstraint("rate_limit_per_minute > 0", name="ck_ai_products_rate_limit_positive"),
        CheckConstraint("monthly_budget_idr > 0", name="ck_ai_products_monthly_budget_positive"),
        # Partial unique index: nama hanya unik untuk produk yang belum dihapus.
        # Produk terhapus boleh namanya dipakai ulang untuk produk baru.
        Index(
            "uq_ai_products_active_name",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_type: Mapped[ProviderType] = mapped_column(
        Enum(
            ProviderType,
            name="ai_product_provider_type_enum",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # BYTEA di PostgreSQL. LargeBinary supaya test SQLite tetap bisa menyimpannya.
    credential_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credential_hint: Mapped[str] = mapped_column(String(4), nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_budget_idr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[LastTestStatus | None] = mapped_column(
        Enum(
            LastTestStatus,
            name="ai_product_last_test_status_enum",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_error_category: Mapped[ConnectionTestErrorCategory | None] = mapped_column(
        Enum(
            ConnectionTestErrorCategory,
            name="ai_product_connection_test_error_category_enum",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
