"""merge case_versions and staging heads

Revision ID: b3e8c1a74d02
Revises: f4b2c8e19a30, d8b4f6a2c913
Create Date: 2026-10-10 15:30:00.000000

SCRUM-136 landed beside the audit and ai_products soft-delete chain.
This empty revision gives alembic upgrade head a single tip.
"""

from collections.abc import Sequence

revision: str = "b3e8c1a74d02"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = ("f4b2c8e19a30", "d8b4f6a2c913")  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
