"""add deleted_at and error_category to ai_products

Revision ID: f2a9d3c1e074
Revises: e8f1a2c44b10
Create Date: 2026-10-07 12:00:00.000000

Perubahan:
- Tambah kolom deleted_at (nullable) untuk soft delete
- Tambah kolom last_test_error_category (nullable enum) untuk kategori kegagalan uji koneksi
- Hapus global unique constraint pada name
- Tambah partial unique index: name unik hanya untuk produk yang belum dihapus (deleted_at IS NULL)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a9d3c1e074"  # pragma: allowlist secret
down_revision: str | None = "e8f1a2c44b10"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

error_category_enum = postgresql.ENUM(
    "access_denied",
    "model_not_found",
    "timeout",
    "unreachable",
    "unknown",
    name="ai_product_connection_test_error_category_enum",
    create_type=False,
)


def upgrade() -> None:
    error_category_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "ai_products",
        sa.Column("last_test_error_category", error_category_enum, nullable=True),
    )
    op.add_column(
        "ai_products",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Ganti global unique constraint dengan partial unique index.
    # Unique index hanya berlaku untuk produk yang belum dihapus.
    op.drop_constraint("uq_ai_products_name", "ai_products", type_="unique")
    op.create_index(
        "uq_ai_products_active_name",
        "ai_products",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_products_active_name", table_name="ai_products")
    op.create_unique_constraint("uq_ai_products_name", "ai_products", ["name"])
    op.drop_column("ai_products", "deleted_at")
    op.drop_column("ai_products", "last_test_error_category")
    error_category_enum.drop(op.get_bind(), checkfirst=True)
