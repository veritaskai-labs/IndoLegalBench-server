"""create cases table

Revision ID: c3a91e7b4d02
Revises: a4c8e2b17d90
Create Date: 2026-09-27 15:45:00.000000

SCRUM-105. JSONB on PostgreSQL, plain JSON on SQLite.
TODO(SCRUM-103): columns follow the ticket text, not a signed contract.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a91e7b4d02"  # pragma: allowlist secret
down_revision: str | None = "a4c8e2b17d90"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# create_type=False: tipe dibuat sekali lewat .create(). CREATE TABLE tidak
# boleh mengeluarkan CREATE TYPE lagi; PostgreSQL menolak duplikat itu di
# dalam transaksi Alembic yang sama. SQLite mengabaikan CREATE/DROP TYPE.
# Create each enum once via .create(). CREATE TABLE must not emit CREATE TYPE
# again; PostgreSQL rejects that duplicate inside the same Alembic transaction.
# SQLite ignores CREATE/DROP TYPE.
split_tag_enum = postgresql.ENUM(
    "dev",
    "test",
    name="case_split_tag_enum",
    create_type=False,
)
status_enum = postgresql.ENUM(
    "draft",
    "in_review",
    "needs_revision",
    "approved",
    name="case_status_enum",
    create_type=False,
)


def upgrade() -> None:
    split_tag_enum.create(op.get_bind(), checkfirst=True)
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_code", sa.String(length=64), nullable=False),
        sa.Column("suite_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("legal_refs", json_type, nullable=False),
        sa.Column("answer_criteria", json_type, nullable=False),
        sa.Column("traps", json_type, nullable=False),
        sa.Column("split_tag", split_tag_enum, nullable=False),
        sa.Column("status", status_enum, server_default="draft", nullable=False),
        sa.Column("completeness", json_type, nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_cases_created_by_users"),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], name="fk_cases_suite_id_suites"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_cases_updated_by_users"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_code", name="uq_cases_case_code"),
    )
    op.create_index("ix_cases_suite_id", "cases", ["suite_id"])
    op.create_index("ix_cases_status", "cases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_cases_status", table_name="cases")
    op.drop_index("ix_cases_suite_id", table_name="cases")
    op.drop_table("cases")
    status_enum.drop(op.get_bind(), checkfirst=True)
    split_tag_enum.drop(op.get_bind(), checkfirst=True)
