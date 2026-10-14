"""move case content onto case_versions

Revision ID: f4b2c8e19a30
Revises: e8f1a2c44b10
Create Date: 2026-10-06 08:00:00.000000

SCRUM-136. Each existing case becomes version 1. Approved rows are
immutable: a trigger rejects UPDATE and DELETE when status is approved.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b2c8e19a30"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "e8f1a2c44b10"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

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

_BACKFILL = sa.text(
    """
    INSERT INTO case_versions (
        id, case_id, version_no, status, split_tag, content,
        created_by, created_at, updated_at, based_on_version_id
    )
    SELECT
        gen_random_uuid(),
        id,
        1,
        status,
        split_tag,
        jsonb_build_object(
            'title', title,
            'question', question,
            'category', category,
            'legal_refs', legal_refs,
            'answer_criteria', answer_criteria,
            'traps', traps,
            'completeness', completeness
        ),
        created_by,
        created_at,
        updated_at,
        NULL
    FROM cases
    """
)

_POINT_CURRENT = sa.text(
    """
    UPDATE cases AS kasus
    SET current_version_id = versi.id
    FROM case_versions AS versi
    WHERE versi.case_id = kasus.id
    """
)

_POINT_APPROVED = sa.text(
    """
    UPDATE cases AS kasus
    SET latest_approved_version_id = versi.id
    FROM case_versions AS versi
    WHERE versi.case_id = kasus.id
      AND versi.status::text = 'approved'
    """
)

_RESTORE_BODY = sa.text(
    """
    UPDATE cases AS kasus
    SET
        title = versi.content ->> 'title',
        question = versi.content ->> 'question',
        category = versi.content ->> 'category',
        legal_refs = versi.content -> 'legal_refs',
        answer_criteria = versi.content -> 'answer_criteria',
        traps = versi.content -> 'traps',
        completeness = versi.content -> 'completeness',
        split_tag = versi.split_tag,
        status = versi.status,
        version = versi.version_no
    FROM case_versions AS versi
    WHERE versi.id = COALESCE(kasus.latest_approved_version_id, kasus.current_version_id)
    """
)


def upgrade() -> None:
    op.create_table(
        "case_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("split_tag", split_tag_enum, nullable=False),
        sa.Column("content", json_type, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
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
        sa.Column("based_on_version_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["based_on_version_id"],
            ["case_versions.id"],
            name="fk_case_versions_based_on_version_id",
        ),
        # Deferred with the pointers on cases. PostgreSQL checks both ends
        # at commit, so the case row and version 1 can be inserted together.
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name="fk_case_versions_case_id_cases",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_case_versions_created_by_users"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "version_no", name="uq_case_versions_case_id_version_no"),
    )
    op.create_index("ix_case_versions_case_id", "case_versions", ["case_id"])
    op.add_column("cases", sa.Column("current_version_id", sa.Uuid(), nullable=True))
    op.add_column("cases", sa.Column("latest_approved_version_id", sa.Uuid(), nullable=True))
    op.execute(_BACKFILL)
    op.execute(_POINT_CURRENT)
    op.execute(_POINT_APPROVED)
    # The backfill queued deferred FK checks on case_versions. PostgreSQL
    # refuses ALTER TABLE on a table with pending trigger events, and alembic
    # runs every revision in one transaction, so later revisions that alter
    # case_versions failed on any database that already had cases. Run the
    # checks now instead of at commit.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.alter_column("cases", "current_version_id", nullable=False)
    op.create_foreign_key(
        "fk_cases_current_version_id",
        "cases",
        "case_versions",
        ["current_version_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_cases_latest_approved_version_id",
        "cases",
        "case_versions",
        ["latest_approved_version_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_index("ix_cases_status", table_name="cases")
    op.drop_column("cases", "title")
    op.drop_column("cases", "question")
    op.drop_column("cases", "category")
    op.drop_column("cases", "legal_refs")
    op.drop_column("cases", "answer_criteria")
    op.drop_column("cases", "traps")
    op.drop_column("cases", "split_tag")
    op.drop_column("cases", "status")
    op.drop_column("cases", "completeness")
    op.drop_column("cases", "version")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION case_versions_reject_approved_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status::text = 'approved' THEN
                RAISE EXCEPTION 'approved case version is immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_versions_reject_approved_mutation
        BEFORE UPDATE OR DELETE ON case_versions
        FOR EACH ROW
        EXECUTE FUNCTION case_versions_reject_approved_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS case_versions_reject_approved_mutation ON case_versions")
    op.execute("DROP FUNCTION IF EXISTS case_versions_reject_approved_mutation()")
    op.add_column("cases", sa.Column("title", sa.String(length=300), nullable=True))
    op.add_column("cases", sa.Column("question", sa.Text(), nullable=True))
    op.add_column("cases", sa.Column("category", sa.String(length=120), nullable=True))
    op.add_column("cases", sa.Column("legal_refs", json_type, nullable=True))
    op.add_column("cases", sa.Column("answer_criteria", json_type, nullable=True))
    op.add_column("cases", sa.Column("traps", json_type, nullable=True))
    op.add_column("cases", sa.Column("split_tag", split_tag_enum, nullable=True))
    op.add_column(
        "cases",
        sa.Column("status", status_enum, server_default="draft", nullable=True),
    )
    op.add_column("cases", sa.Column("completeness", json_type, nullable=True))
    op.add_column(
        "cases",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=True),
    )
    op.execute(_RESTORE_BODY)
    op.alter_column("cases", "title", nullable=False)
    op.alter_column("cases", "question", nullable=False)
    op.alter_column("cases", "legal_refs", nullable=False)
    op.alter_column("cases", "answer_criteria", nullable=False)
    op.alter_column("cases", "traps", nullable=False)
    op.alter_column("cases", "split_tag", nullable=False)
    op.alter_column("cases", "status", nullable=False)
    op.alter_column("cases", "completeness", nullable=False)
    op.alter_column("cases", "version", nullable=False)
    op.create_index("ix_cases_status", "cases", ["status"])
    op.drop_constraint("fk_cases_latest_approved_version_id", "cases", type_="foreignkey")
    op.drop_constraint("fk_cases_current_version_id", "cases", type_="foreignkey")
    op.drop_column("cases", "latest_approved_version_id")
    op.drop_column("cases", "current_version_id")
    op.drop_index("ix_case_versions_case_id", table_name="case_versions")
    op.drop_table("case_versions")
