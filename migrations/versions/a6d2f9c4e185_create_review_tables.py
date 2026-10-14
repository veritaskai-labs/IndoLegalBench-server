"""create review_rounds, review_assignments, review_verdicts

Revision ID: a6d2f9c4e185
Revises: e1a7c4b92f50
Create Date: 2026-10-11 09:00:00.000000

PBI-6, SCRUM-143. Shapes follow D4a. review_rounds also stores case_id,
like suite_snapshot_items, so audit and the Reviewer scope of PBI-18 find
the case without reading case_versions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a6d2f9c4e185"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "e1a7c4b92f50"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

round_status = postgresql.ENUM(
    "awaiting_assignment", "open", "decided", name="review_round_status_enum", create_type=False
)
round_decision = postgresql.ENUM(
    "approved", "revise", name="review_round_decision_enum", create_type=False
)
assignment_source = postgresql.ENUM(
    "system", "admin", name="review_assignment_source_enum", create_type=False
)
assignment_status = postgresql.ENUM(
    "active", "replaced", "void", name="review_assignment_status_enum", create_type=False
)
verdict_decision = postgresql.ENUM(
    "approve", "revise", name="review_verdict_decision_enum", create_type=False
)
_ENUMS = (round_status, round_decision, assignment_source, assignment_status, verdict_decision)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "review_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("case_version_id", sa.Uuid(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("status", round_status, nullable=False),
        sa.Column("decision", round_decision, nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_review_rounds_case_id"),
        sa.ForeignKeyConstraint(
            ["case_version_id"], ["case_versions.id"], name="fk_review_rounds_case_version_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_version_id", "round_no", name="uq_review_rounds_case_version_id_round_no"
        ),
    )
    op.create_index("ix_review_rounds_case_id", "review_rounds", ["case_id"])

    op.create_table(
        "review_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_round_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("source", assignment_source, nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", assignment_status, nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["review_round_id"],
            ["review_rounds.id"],
            name="fk_review_assignments_review_round_id",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name="fk_review_assignments_reviewer_id"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], name="fk_review_assignments_assigned_by"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_review_assignments_active_reviewer",
        "review_assignments",
        ["review_round_id", "reviewer_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_review_assignments_reviewer_id", "review_assignments", ["reviewer_id"])

    op.create_table(
        "review_verdicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("decision", verdict_decision, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("carried_from_verdict_id", sa.Uuid(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision <> 'revise' OR (comment IS NOT NULL AND length(trim(comment)) > 0)",
            name="ck_review_verdicts_revise_needs_comment",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["review_assignments.id"], name="fk_review_verdicts_assignment_id"
        ),
        sa.ForeignKeyConstraint(
            ["carried_from_verdict_id"],
            ["review_verdicts.id"],
            name="fk_review_verdicts_carried_from_verdict_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id"),
    )


def downgrade() -> None:
    op.drop_table("review_verdicts")
    op.drop_index("ix_review_assignments_reviewer_id", table_name="review_assignments")
    op.drop_index("uq_review_assignments_active_reviewer", table_name="review_assignments")
    op.drop_table("review_assignments")
    op.drop_index("ix_review_rounds_case_id", table_name="review_rounds")
    op.drop_table("review_rounds")
    bind = op.get_bind()
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
