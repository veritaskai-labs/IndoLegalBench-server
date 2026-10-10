"""suite snapshots

Revision ID: c8d1e4a73b20
Revises: b3e8c1a74d02
Create Date: 2026-10-08 04:50:00.000000

SCRUM-137. A snapshot freezes the approved cases of a suite. Each item
stores case_version_id and a JSON copy of the case body.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8d1e4a73b20"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "b3e8c1a74d02"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "suite_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suite_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_suite_snapshots_created_by_users"),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], name="fk_suite_snapshots_suite_id_suites"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suite_snapshots_suite_id", "suite_snapshots", ["suite_id"])
    op.create_table(
        "suite_snapshot_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_version_id", sa.Uuid(), nullable=False),
        sa.Column("body", json_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name="fk_suite_snapshot_items_case_id_cases"
        ),
        sa.ForeignKeyConstraint(
            ["case_version_id"],
            ["case_versions.id"],
            name="fk_suite_snapshot_items_case_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["suite_snapshots.id"],
            name="fk_suite_snapshot_items_snapshot_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suite_snapshot_items_snapshot_id", "suite_snapshot_items", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_suite_snapshot_items_snapshot_id", table_name="suite_snapshot_items")
    op.drop_table("suite_snapshot_items")
    op.drop_index("ix_suite_snapshots_suite_id", table_name="suite_snapshots")
    op.drop_table("suite_snapshots")
