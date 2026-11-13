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
from app.shared.text import sql_not_blank

# JSONB on PostgreSQL, plain JSON on SQLite so the tests keep running.
# none_as_null: None disimpan sebagai SQL NULL, bukan JSON null. C1 (D1) mensyaratkan
# model_params IS NULL untuk produk Manual, jadi JSON null membuat pindah jenis gagal.
_JSONB = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


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
                AND price_cache_read_usd_per_mtok IS NULL
                AND price_cache_write_usd_per_mtok IS NULL
                AND price_cache_write_long_usd_per_mtok IS NULL
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
    price_cache_read_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_cache_write_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_cache_write_long_usd_per_mtok: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )

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


class JudgeProvider(Base):
    """Provider LLM penilai (PBI-14 AC1, AC6; D6; SCRUM-258).

    Tabel sendiri, bukan baris ai_products, supaya penilai tidak muncul di pilihan
    produk, dashboard, dan laporan (catatan SA 6 Nov). Kredensial memakai masking
    yang sama dengan ai_products. Harga wajib supaya biaya tiap baris penilai
    diketahui, dan anggaran bulanannya terpisah dari produk (klien No. 63 A).
    """

    __tablename__ = "judge_providers"
    __table_args__ = (
        CheckConstraint(sql_not_blank("name"), name="ck_judge_providers_name"),
        CheckConstraint(sql_not_blank("base_url"), name="ck_judge_providers_base_url"),
        CheckConstraint(sql_not_blank("model_name"), name="ck_judge_providers_model_name"),
        CheckConstraint("length(credential_hint) = 4", name="ck_judge_providers_credential_hint"),
        CheckConstraint(
            "price_input_usd_per_mtok >= 0 AND price_output_usd_per_mtok >= 0",
            name="ck_judge_providers_prices",
        ),
        CheckConstraint("monthly_budget_idr > 0", name="ck_judge_providers_monthly_budget"),
        # Nama tidak dipakai ulang, juga oleh penilai nonaktif yang masih dirujuk sesi lama.
        UniqueConstraint("name", name="uq_judge_providers_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_type: Mapped[ProviderType] = mapped_column(
        Enum(ProviderType, name="ai_product_provider_type_enum", values_callable=_enum_values),
        nullable=False,
    )
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    credential_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credential_hint: Mapped[str] = mapped_column(String(4), nullable=False)
    price_input_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    price_output_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    monthly_budget_idr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
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
