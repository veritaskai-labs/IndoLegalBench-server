"""Add per-attempt start and finish timestamps. SCRUM-76 SCRUM-259."""

import sqlalchemy as sa
from alembic import op

revision = "b3c91d7a5e24"  # pragma: allowlist secret — Alembic revision identifier
down_revision = "57f872b138f0"  # pragma: allowlist secret — Alembic parent identifier
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_attempts_finished_needs_started"


def upgrade():
    # Nullable on purpose: existing attempts keep NULL and are not backfilled (AC8).
    op.add_column("attempts", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("attempts", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        _CONSTRAINT, "attempts", "finished_at IS NULL OR started_at IS NOT NULL"
    )


def downgrade():
    recorded = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM attempts "
            "WHERE started_at IS NOT NULL OR finished_at IS NOT NULL)"
        )
    )
    if recorded:
        raise RuntimeError("Cannot downgrade while attempt timestamps exist")
    op.drop_constraint(_CONSTRAINT, "attempts", type_="check")
    op.drop_column("attempts", "finished_at")
    op.drop_column("attempts", "started_at")
