"""merge audit_logs and ai_product soft delete heads

Revision ID: d8b4f6a2c913
Revises: c5e7a1d93f40, f2a9d3c1e074
Create Date: 2026-10-09 09:00:00.000000

The audit chain (PBI-18) and the ai_products soft delete (SCRUM-133) both
descend from e8f1a2c44b10. This empty revision gives alembic upgrade head a
single tip again. They touch different tables, so the order does not matter.
"""

from collections.abc import Sequence

revision: str = "d8b4f6a2c913"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = ("c5e7a1d93f40", "f2a9d3c1e074")  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
