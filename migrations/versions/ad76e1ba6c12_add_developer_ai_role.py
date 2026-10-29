"""SCRUM-183: add the developer_ai value to user_role_enum.

PostgreSQL 12+ allows ADD VALUE inside a transaction, as long as the new
value is not used in that same transaction. Nothing here uses it.

PostgreSQL cannot drop an enum value, so the downgrade rebuilds the type.
It refuses while a user still has the role: mapping those users to viewer
would let them read test-set content through version history and snapshots.

Revision ID: ad76e1ba6c12
Revises: e6d7b1a03c24
"""
import sqlalchemy as sa
from alembic import op

revision = "ad76e1ba6c12"  # pragma: allowlist secret
down_revision = "e6d7b1a03c24"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'developer_ai'")


def downgrade():
    bind = op.get_bind()
    jumlah = bind.execute(sa.text("SELECT count(*) FROM users WHERE role = 'developer_ai'")).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} user(s) still have role developer_ai. Give them another role "
            "by hand before downgrading (deactivating them is not enough)."
        )
    op.execute("ALTER TYPE user_role_enum RENAME TO user_role_enum_old")
    op.execute("CREATE TYPE user_role_enum AS ENUM ('author', 'reviewer', 'admin', 'viewer')")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role_enum "
        "USING role::text::user_role_enum"
    )
    op.execute("DROP TYPE user_role_enum_old")
