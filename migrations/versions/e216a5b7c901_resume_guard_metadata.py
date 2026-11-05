"""Persist resume verification and long-stop evidence. SCRUM-77 SCRUM-216."""

import sqlalchemy as sa
from alembic import op

revision = "e216a5b7c901"  # pragma: allowlist secret — Alembic revision identifier
down_revision = "d49a1f2b3c60"  # pragma: allowlist secret — Alembic parent identifier
branch_labels = None
depends_on = None


def upgrade():
    for name in (
        "resumed_after_long_pause",
        "model_version_unverified",
        "resume_version_check_pending",
    ):
        op.add_column(
            "measurement_sessions",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.add_column(
        "measurement_sessions", sa.Column("resume_error_code", sa.String(64), nullable=True)
    )


def downgrade():
    evidence = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM measurement_sessions WHERE resumed_after_long_pause "
            "OR model_version_unverified OR resume_version_check_pending OR resume_error_code IS NOT NULL)"
        )
    )
    if evidence:
        raise RuntimeError("Cannot downgrade while resume verification evidence exists")
    for name in (
        "resume_error_code",
        "resume_version_check_pending",
        "model_version_unverified",
        "resumed_after_long_pause",
    ):
        op.drop_column("measurement_sessions", name)
