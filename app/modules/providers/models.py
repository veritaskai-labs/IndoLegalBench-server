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
    JSON,
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
    UniqueConstraint,
    Uuid,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class ProviderType(StrEnum):
    """ProviderType:
    - openai_compatible: OpenAI chat completions (DeepSeek, GPT). Bearer auth. The stored URL is posted as-is.
    - gemini_interactions: Gemini Interactions API. Header x-goog-api-key.
    - anthropic_messages: Anthropic Messages API. Headers x-api-key and anthropic-version.
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI_INTERACTIONS = "gemini_interactions"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class MeasurementType(StrEnum):
    """MeasurementType:
    - automatic: dijalankan otomatis oleh sistem via API
    - manual: dijalankan manual dan dicatat oleh tim legal via situs
    """

    AUTOMATIC = "automatic"
    MANUAL = "manual"


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
    """Produk AI yang akan diukur, plus kredensial atau catatan akses manual."""

    __tablename__ = "ai_products"
    __table_args__ = (
        CheckConstraint(
            "rate_limit_per_minute IS NULL OR rate_limit_per_minute > 0",
            name="ck_ai_products_rate_limit_positive",
        ),
        CheckConstraint(
            "monthly_budget_idr IS NULL OR monthly_budget_idr > 0",
            name="ck_ai_products_monthly_budget_positive",
        ),
        # C1 CHECK ck_ai_products_type_fields
        CheckConstraint(
            """(
                measurement_type = 'automatic'
                AND provider_type IS NOT NULL
                AND base_url IS NOT NULL
                AND model_name IS NOT NULL
                AND credential_encrypted IS NOT NULL
                AND credential_hint IS NOT NULL
                AND rate_limit_per_minute IS NOT NULL
                AND monthly_budget_idr IS NOT NULL
                AND vendor_name IS NULL
                AND site_url IS NULL
                AND access_note IS NULL
                AND manual_params IS NULL
            ) OR (
                measurement_type = 'manual'
                AND vendor_name IS NOT NULL
                AND site_url IS NOT NULL
                AND provider_type IS NULL
                AND base_url IS NULL
                AND model_name IS NULL
                AND credential_encrypted IS NULL
                AND credential_hint IS NULL
                AND rate_limit_per_minute IS NULL
                AND monthly_budget_idr IS NULL
                AND last_test_at IS NULL
                AND last_test_status IS NULL
                AND last_test_message IS NULL
                AND last_test_error_category IS NULL
                AND model_params IS NULL
                AND price_input_usd_per_mtok IS NULL
                AND price_output_usd_per_mtok IS NULL
            )""",
            name="ck_ai_products_type_fields",
        ),
        # C3: nama produk unik selamanya, termasuk produk nonaktif dan soft-deleted.
        UniqueConstraint("name", name="uq_ai_products_name"),
        # C4: daftar pilihan disaring per jenis dan status aktif (produk yang belum dihapus)
        Index(
            "ix_ai_products_type_active",
            "measurement_type",
            "is_active",
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    measurement_type: Mapped[MeasurementType] = mapped_column(
        Enum(
            MeasurementType,
            name="ai_product_measurement_type_enum",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=MeasurementType.AUTOMATIC,
        server_default=MeasurementType.AUTOMATIC.value,
    )

    # Khusus Otomatis (wajib terisi untuk automatic, NULL untuk manual lewat C1)
    provider_type: Mapped[ProviderType | None] = mapped_column(
        Enum(
            ProviderType,
            name="ai_product_provider_type_enum",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # BYTEA di PostgreSQL. LargeBinary supaya test SQLite tetap bisa menyimpannya.
    credential_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    credential_hint: Mapped[str | None] = mapped_column(String(4), nullable=True)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_budget_idr: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
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
    model_params: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    price_input_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_output_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    # Khusus Manual (wajib terisi vendor_name & site_url untuk manual, NULL untuk automatic)
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    site_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    access_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_params: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bersama
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
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
