"""LLM judge providers, and the judge FKs of measurement sessions and grading runs.

SCRUM-258, PBI-14 AC1, AC6, D6, with the decisions of 6-7 Nov 2026.

judge_providers is its own table in the providers module, not rows of
ai_products, so a judge never shows up in product pickers, dashboards or
reports (SA note 6 Nov). It reuses the provider_type enum and the credential
masking of ai_products (credential_encrypted + four-character credential_hint).
Prices are required so the cost of every judge row is known (AC6), and the
monthly budget is the separate judge budget of client answer No. 63 A.

Before this revision POST /measurements accepted any uuid as judge_provider_id
and grading_runs.judge_provider_id had no FK (#111). Ids without a judge row are
set to NULL first and the counts are logged. Started sessions and grading runs
are protected by guard triggers (16014b26f0bf, 8ead8669ff79), so those triggers
are disabled only for that UPDATE inside this migration's transaction: the
nulled value never pointed to a real judge. The downgrade is refused while any
judge exists, because dropping the table would silently drop the judge of
every session.

Revision ID: 4f844618b782
Revises: c0e745d15998
"""

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.shared.text import sql_not_blank

revision = "4f844618b782"  # pragma: allowlist secret
down_revision = "c0e745d15998"  # pragma: allowlist secret
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

provider_type_enum = postgresql.ENUM(name="ai_product_provider_type_enum", create_type=False)

# (tabel, trigger penjaga, nama FK)
_RUJUKAN = (
    (
        "measurement_sessions",
        "measurement_sessions_guard_update",
        "fk_measurement_sessions_judge_provider",
    ),
    ("grading_runs", "grading_runs_guard", "fk_grading_runs_judge_provider"),
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.create_table(
        "judge_providers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "provider_type",
            provider_type_enum if is_postgres else sa.String(32),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("credential_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("credential_hint", sa.String(length=4), nullable=False),
        sa.Column("price_input_usd_per_mtok", sa.Numeric(12, 4), nullable=False),
        sa.Column("price_output_usd_per_mtok", sa.Numeric(12, 4), nullable=False),
        sa.Column("monthly_budget_idr", sa.Numeric(18, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(sql_not_blank("name"), name="ck_judge_providers_name"),
        sa.CheckConstraint(sql_not_blank("base_url"), name="ck_judge_providers_base_url"),
        sa.CheckConstraint(sql_not_blank("model_name"), name="ck_judge_providers_model_name"),
        sa.CheckConstraint(
            "length(credential_hint) = 4", name="ck_judge_providers_credential_hint"
        ),
        sa.CheckConstraint(
            "price_input_usd_per_mtok >= 0 AND price_output_usd_per_mtok >= 0",
            name="ck_judge_providers_prices",
        ),
        sa.CheckConstraint("monthly_budget_idr > 0", name="ck_judge_providers_monthly_budget"),
        sa.UniqueConstraint("name", name="uq_judge_providers_name"),
    )

    for table, guard, fk in _RUJUKAN:
        jumlah = _kosongkan_yatim(table, guard if is_postgres else None)
        log.warning("%d %s judge_provider_id set to NULL (no judge_providers row)", jumlah, table)
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                fk, "judge_providers", ["judge_provider_id"], ["id"], ondelete="RESTRICT"
            )


def _kosongkan_yatim(table: str, guard: str | None) -> int:
    """NULL untuk judge_provider_id tanpa baris penilai; trigger penjaga dimatikan sebentar.

    ALTER TABLE ... DISABLE TRIGGER ikut transaksi migrasi di PostgreSQL, jadi kalau
    migrasi gagal trigger tetap hidup.
    """
    if guard is not None:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {guard}")
    jumlah = (
        op.get_bind()
        .execute(
            sa.text(
                f"UPDATE {table} SET judge_provider_id = NULL "
                "WHERE judge_provider_id IS NOT NULL"
            )
        )
        .rowcount
    )
    if guard is not None:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {guard}")
    return jumlah


def downgrade() -> None:
    jumlah = op.get_bind().execute(sa.text("SELECT count(*) FROM judge_providers")).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} judge provider(s) exist. Sessions and grading runs point to them; "
            "export and remove them by hand before downgrading."
        )
    for table, _guard, fk in _RUJUKAN:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(fk, type_="foreignkey")
    op.drop_table("judge_providers")
