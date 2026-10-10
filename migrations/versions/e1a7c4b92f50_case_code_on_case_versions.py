"""store case_code on each case version

Revision ID: e1a7c4b92f50
Revises: c8d1e4a73b20
Create Date: 2026-10-10 17:30:00.000000

SCRUM-137. History and compare read case_code from the version row so a
later rename of the live case does not rewrite older sides of a diff.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a7c4b92f50"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "c8d1e4a73b20"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("case_versions", sa.Column("case_code", sa.String(length=64), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE case_versions AS versi
            SET case_code = kasus.case_code
            FROM cases AS kasus
            WHERE kasus.id = versi.case_id
            """
        )
    )
    op.alter_column("case_versions", "case_code", nullable=False)


def downgrade() -> None:
    op.drop_column("case_versions", "case_code")
