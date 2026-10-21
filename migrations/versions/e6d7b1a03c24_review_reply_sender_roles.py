"""Preserve sender roles for two-way review discussions.

Revision ID: e6d7b1a03c24
Revises: c4a3d2e1f905
"""
import sqlalchemy as sa
from alembic import op

revision = "e6d7b1a03c24"  # pragma: allowlist secret
down_revision = "c4a3d2e1f905"  # pragma: allowlist secret
branch_labels = None
depends_on = None
TABLE = "review_comment_replies"


def upgrade():
    op.add_column(TABLE, sa.Column("author_role", sa.String(16), nullable=False, server_default="author"))
    op.create_check_constraint("ck_review_reply_author_role", TABLE, "author_role IN ('author', 'reviewer')")


def downgrade():
    op.drop_constraint("ck_review_reply_author_role", TABLE, type_="check")
    op.drop_column(TABLE, "author_role")
