"""PBI-24 (SCRUM-195): add the contributor value to user_role_enum.

First of two steps. This one only adds the value, so the app can grant the
Contributor role while author and reviewer still exist. Moving the accounts
over and dropping the old values comes in the next migration.

PostgreSQL 12+ allows ADD VALUE inside a transaction, as long as the new
value is not used in that same transaction. Nothing here uses it.

PostgreSQL cannot drop an enum value, so the downgrade rebuilds the type.
It refuses while a user still has the role: guessing whether they were an
author or a reviewer would change what they are allowed to do.

Revision ID: 4a10dea5a620
Revises: c7e19d0b5a42
"""
import sqlalchemy as sa
from alembic import op

revision = "4a10dea5a620"  # pragma: allowlist secret
down_revision = "c7e19d0b5a42"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'contributor'")


def downgrade():
    bind = op.get_bind()
    jumlah = bind.execute(sa.text("SELECT count(*) FROM users WHERE role = 'contributor'")).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} user(s) still have role contributor. Give them another role "
            "by hand before downgrading (deactivating them is not enough)."
        )
    op.execute("ALTER TYPE user_role_enum RENAME TO user_role_enum_old")
    op.execute(
        "CREATE TYPE user_role_enum AS ENUM "
        "('author', 'reviewer', 'admin', 'viewer', 'developer_ai')"
    )
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role_enum "
        "USING role::text::user_role_enum"
    )
    op.execute("DROP TYPE user_role_enum_old")
