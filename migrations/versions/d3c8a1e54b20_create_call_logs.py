"""SCRUM-209: call_logs for each provider attempt.

attempt_id has no foreign key yet.
TODO(SCRUM-205): add the foreign key when the attempt table is on staging.

Revision ID: d3c8a1e54b20
Revises: a1f8c7e3d092
"""

import sqlalchemy as sa
from alembic import op

revision = "d3c8a1e54b20"  # pragma: allowlist secret
down_revision = "a1f8c7e3d092"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_idr", sa.Numeric(18, 2), nullable=True),
        sa.Column("usd_idr_rate", sa.Numeric(18, 4), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("failure_kind", sa.String(length=64), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_call_logs_product_id", "call_logs", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_call_logs_product_id", table_name="call_logs")
    op.drop_table("call_logs")
