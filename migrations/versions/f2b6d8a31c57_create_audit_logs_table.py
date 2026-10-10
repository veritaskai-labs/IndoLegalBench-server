"""create audit_logs table

Revision ID: f2b6d8a31c57
Revises: e8f1a2c44b10
Create Date: 2026-10-08 10:00:00.000000

PBI-18. Columns and indexes follow diagram D5b (ERD Audit Log).
Immutability (grants and trigger) lands in its own revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2b6d8a31c57"  # pragma: allowlist secret
down_revision: str | None = "e8f1a2c44b10"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
bigint_pk = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", bigint_pk, sa.Identity(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("before", json_type, nullable=True),
        sa.Column("after", json_type, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_occurred_at",
        "audit_logs",
        [sa.text("occurred_at DESC")],
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index(
        "ix_audit_logs_actor_occurred_at", "audit_logs", ["actor_user_id", "occurred_at"]
    )
    op.create_index("ix_audit_logs_case_occurred_at", "audit_logs", ["case_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_case_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_occurred_at", table_name="audit_logs")
    op.drop_table("audit_logs")
