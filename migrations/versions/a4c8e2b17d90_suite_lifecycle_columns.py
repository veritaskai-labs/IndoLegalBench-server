"""suite lifecycle columns for SCRUM-99

Revision ID: a4c8e2b17d90
Revises: dcdf0157419a
Create Date: 2026-09-23 16:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c8e2b17d90"  # pragma: allowlist secret
down_revision: str | None = "dcdf0157419a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

suite_status_enum = sa.Enum("active", "archived", name="suite_status_enum")


def upgrade() -> None:
    suite_status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("suites", sa.Column("created_by", sa.Uuid(), nullable=False))
    op.add_column("suites", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_suites_created_by_users",
        "suites",
        "users",
        ["created_by"],
        ["id"],
    )
    op.alter_column(
        "suites",
        "status",
        existing_type=sa.String(length=20),
        type_=suite_status_enum,
        postgresql_using="status::suite_status_enum",
        existing_nullable=False,
    )
    op.drop_constraint("suites_name_key", "suites", type_="unique")
    op.create_index("uq_suites_name_ci", "suites", [sa.text("lower(name)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_suites_name_ci", table_name="suites")
    op.create_unique_constraint("suites_name_key", "suites", ["name"])
    op.alter_column(
        "suites",
        "status",
        existing_type=suite_status_enum,
        type_=sa.String(length=20),
        postgresql_using="status::text",
        existing_nullable=False,
    )
    op.drop_constraint("fk_suites_created_by_users", "suites", type_="foreignkey")
    op.drop_column("suites", "deleted_at")
    op.drop_column("suites", "created_by")
    suite_status_enum.drop(op.get_bind(), checkfirst=True)
