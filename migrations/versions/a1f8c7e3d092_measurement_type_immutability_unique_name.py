"""Add measurement_type, manual columns, immutability trigger, and unique name to ai_products.

Revision ID: a1f8c7e3d092
Revises: e6d7b1a03c24
Create Date: 2026-10-27 14:30:00.000000

PBI-23 SCRUM-177 (SCRUM-200):
- Kolom measurement_type (automatic/manual, wajib) di ai_products, default 'automatic'
- Kolom khusus manual: vendor_name, site_url, access_note, manual_params
- Kolom khusus otomatis: model_params, price_input_usd_per_mtok, price_output_usd_per_mtok
- Kolom khusus otomatis dilonggarkan menjadi nullable di level kolom
- CHECK constraint ck_ai_products_type_fields (C1)
- Trigger BEFORE UPDATE untuk immutability measurement_type setelah ada riwayat pengukuran (C2)
- Unique constraint uq_ai_products_name tanpa WHERE clause (C3, nama unik selamanya)
- Index ix_ai_products_type_active (C4)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1f8c7e3d092"  # pragma: allowlist secret
down_revision: str | None = "e6d7b1a03c24"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

measurement_type_enum = postgresql.ENUM(
    "automatic",
    "manual",
    name="ai_product_measurement_type_enum",
    create_type=False,
)

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

C1_CHECK_SQL = """(
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
)"""


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. Cek nama ganda yang sudah ada sebelum constraint dipasang (AC4)
    duplicates = bind.execute(
        sa.text("SELECT name, count(*) FROM ai_products GROUP BY name HAVING count(*) > 1")
    ).fetchall()
    if duplicates:
        names = [f"'{row[0]}' ({row[1]} baris)" for row in duplicates]
        raise RuntimeError(
            f"Migrasi dibatalkan: ditemukan nama produk duplikat sebelum memasang constraint unik: {', '.join(names)}"
        )

    # 2. Ganti partial unique index dengan global unique constraint (C3: unik selamanya)
    op.drop_index("uq_ai_products_active_name", table_name="ai_products")
    op.create_unique_constraint("uq_ai_products_name", "ai_products", ["name"])

    # 3. Buat enum measurement_type jika di postgresql
    if is_postgres:
        measurement_type_enum.create(bind, checkfirst=True)

    # 4. Tambah kolom measurement_type (default 'automatic')
    op.add_column(
        "ai_products",
        sa.Column(
            "measurement_type",
            measurement_type_enum if is_postgres else sa.String(20),
            server_default="automatic",
            nullable=False,
        ),
    )

    # 5. Tambah kolom khusus manual
    op.add_column("ai_products", sa.Column("vendor_name", sa.String(length=200), nullable=True))
    op.add_column("ai_products", sa.Column("site_url", sa.String(length=2048), nullable=True))
    op.add_column("ai_products", sa.Column("access_note", sa.Text(), nullable=True))
    op.add_column("ai_products", sa.Column("manual_params", sa.Text(), nullable=True))

    # 6. Tambah kolom khusus otomatis
    op.add_column("ai_products", sa.Column("model_params", _JSONB, nullable=True))
    op.add_column(
        "ai_products",
        sa.Column("price_input_usd_per_mtok", sa.Numeric(precision=12, scale=4), nullable=True),
    )
    op.add_column(
        "ai_products",
        sa.Column("price_output_usd_per_mtok", sa.Numeric(precision=12, scale=4), nullable=True),
    )

    # 7. Longgarkan kolom khusus otomatis menjadi nullable di level kolom
    if is_postgres:
        for col in [
            "provider_type",
            "base_url",
            "model_name",
            "credential_encrypted",
            "credential_hint",
            "rate_limit_per_minute",
            "monthly_budget_idr",
        ]:
            op.alter_column("ai_products", col, nullable=True)

    # 8. Modifikasi check constraint batas pemakaian agar mengizinkan NULL (untuk manual)
    op.drop_constraint("ck_ai_products_rate_limit_positive", "ai_products", type_="check")
    op.create_check_constraint(
        "ck_ai_products_rate_limit_positive",
        "ai_products",
        "rate_limit_per_minute IS NULL OR rate_limit_per_minute > 0",
    )
    op.drop_constraint("ck_ai_products_monthly_budget_positive", "ai_products", type_="check")
    op.create_check_constraint(
        "ck_ai_products_monthly_budget_positive",
        "ai_products",
        "monthly_budget_idr IS NULL OR monthly_budget_idr > 0",
    )

    # 9. Tambah CHECK constraint C1 (ck_ai_products_type_fields)
    op.create_check_constraint(
        "ck_ai_products_type_fields",
        "ai_products",
        C1_CHECK_SQL,
    )

    # 10. Tambah index ix_ai_products_type_active (C4)
    op.create_index(
        "ix_ai_products_type_active",
        "ai_products",
        ["measurement_type", "is_active"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 11. Pasang database trigger immutability measurement_type (C2)
    if is_postgres:
        op.execute(
            """
            CREATE OR REPLACE FUNCTION ai_products_check_measurement_type_immutability()
            RETURNS trigger AS $$
            DECLARE
                session_count integer;
            BEGIN
                IF OLD.measurement_type IS DISTINCT FROM NEW.measurement_type THEN
                    IF to_regclass('measurement_sessions') IS NOT NULL THEN
                        EXECUTE 'SELECT count(*) FROM measurement_sessions WHERE product_id = $1'
                        INTO session_count
                        USING OLD.id;
                        IF session_count > 0 THEN
                            RAISE EXCEPTION 'MEASUREMENT_TYPE_IMMUTABLE: jenis pengukuran tidak dapat diubah karena produk sudah memiliki riwayat pengukuran'
                                USING ERRCODE = 'check_violation';
                        END IF;
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_ai_products_measurement_type_immutable
            BEFORE UPDATE OF measurement_type ON ai_products
            FOR EACH ROW
            EXECUTE FUNCTION ai_products_check_measurement_type_immutability();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("DROP TRIGGER IF EXISTS trg_ai_products_measurement_type_immutable ON ai_products")
        op.execute("DROP FUNCTION IF EXISTS ai_products_check_measurement_type_immutability()")

    op.drop_index("ix_ai_products_type_active", table_name="ai_products")
    op.drop_constraint("ck_ai_products_type_fields", "ai_products", type_="check")

    op.drop_constraint("ck_ai_products_monthly_budget_positive", "ai_products", type_="check")
    op.create_check_constraint(
        "ck_ai_products_monthly_budget_positive",
        "ai_products",
        "monthly_budget_idr > 0",
    )
    op.drop_constraint("ck_ai_products_rate_limit_positive", "ai_products", type_="check")
    op.create_check_constraint(
        "ck_ai_products_rate_limit_positive",
        "ai_products",
        "rate_limit_per_minute > 0",
    )

    if is_postgres:
        for col in [
            "monthly_budget_idr",
            "rate_limit_per_minute",
            "credential_hint",
            "credential_encrypted",
            "model_name",
            "base_url",
            "provider_type",
        ]:
            op.alter_column("ai_products", col, nullable=False)

    op.drop_column("ai_products", "price_output_usd_per_mtok")
    op.drop_column("ai_products", "price_input_usd_per_mtok")
    op.drop_column("ai_products", "model_params")
    op.drop_column("ai_products", "manual_params")
    op.drop_column("ai_products", "access_note")
    op.drop_column("ai_products", "site_url")
    op.drop_column("ai_products", "vendor_name")
    op.drop_column("ai_products", "measurement_type")

    if is_postgres:
        measurement_type_enum.drop(bind, checkfirst=True)

    op.drop_constraint("uq_ai_products_name", "ai_products", type_="unique")
    op.create_index(
        "uq_ai_products_active_name",
        "ai_products",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

