"""merge judge guard and manual evidence heads

Revision ID: 1d73e6afcfe1
Revises: 0f04577560c0, c4e8a1d7f093
Create Date: 2026-11-08 17:05:30.363641

0f04577560c0 (manual answer evidence, #121) and c4e8a1d7f093 (llm_judge_guard
allows failed judge calls) were written in parallel. This empty revision gives
alembic upgrade head a single tip.
"""

from collections.abc import Sequence

revision: str = "1d73e6afcfe1"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = (
    "0f04577560c0",  # pragma: allowlist secret
    "c4e8a1d7f093",  # pragma: allowlist secret
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
