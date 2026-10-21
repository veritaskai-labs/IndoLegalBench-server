"""K-03 author replies on reviewer comments.

Revision ID: c4a3d2e1f905
Revises: e5b8c2d14a70
"""
import sqlalchemy as sa
from alembic import op

revision = "c4a3d2e1f905"  # pragma: allowlist secret
down_revision = "e5b8c2d14a70"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_comment_replies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("review_assignments.id"), nullable=False),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(body)) > 0 AND length(body) <= 5000", name="ck_review_reply_body"),
    )
    op.create_index("ix_review_comment_replies_assignment_created", "review_comment_replies", ["assignment_id", "created_at"])


def downgrade():
    op.drop_index("ix_review_comment_replies_assignment_created", table_name="review_comment_replies")
    op.drop_table("review_comment_replies")
