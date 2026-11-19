"""Tabel laporan perbandingan, dasar kasus, hasil beku, dan penerbitan. SCRUM-79 SCRUM-300.

D1 "ERD Laporan dan Penerbitan" (PBI-15, PBI-16, PBI-17).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b6f3a2d81c47"  # pragma: allowlist secret
down_revision = "e5b1c7d93a20"  # pragma: allowlist secret
branch_labels = None
depends_on = None

score_scope_enum = postgresql.ENUM(
    "test", "dev", "dev_test", name="report_score_scope_enum", create_type=False
)
score_split_enum = postgresql.ENUM(
    "test", "dev", "combined", name="report_score_split_enum", create_type=False
)
measurement_filter_enum = postgresql.ENUM(
    "automatic", "manual", "all", name="report_measurement_filter_enum", create_type=False
)
measurement_kind_enum = postgresql.ENUM(
    "automatic", "manual", name="report_measurement_kind_enum", create_type=False
)
label_mode_enum = postgresql.ENUM(
    "real_name", "code", name="report_label_mode_enum", create_type=False
)
language_enum = postgresql.ENUM("en", "id", name="report_language_enum", create_type=False)
status_enum = postgresql.ENUM(
    "saved", "published", "superseded", name="report_status_enum", create_type=False
)
# Sudah ada sejak migrasi kasus; dipakai ulang, tidak dibuat atau dihapus di sini.
split_tag_enum = postgresql.ENUM("dev", "test", name="case_split_tag_enum", create_type=False)

_ENUMS = (
    score_scope_enum,
    score_split_enum,
    measurement_filter_enum,
    measurement_kind_enum,
    label_mode_enum,
    language_enum,
    status_enum,
)

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in _ENUMS:
            enum.create(bind, checkfirst=True)

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("score_scope", score_scope_enum, nullable=False),
        sa.Column("has_dev_scores", sa.Boolean(), nullable=False),
        sa.Column("measurement_filter", measurement_filter_enum, nullable=False),
        sa.Column("product_label_mode", label_mode_enum, nullable=False),
        sa.Column("language", language_enum, nullable=False, server_default="en"),
        sa.Column("status", status_enum, nullable=False, server_default="saved"),
        sa.Column(
            "rubric_version_id", sa.Uuid(), sa.ForeignKey("rubric_versions.id"), nullable=False
        ),
        sa.Column("judge_model_name", sa.String(200), nullable=False),
        sa.Column("case_basis_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "report_sessions",
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id"), primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("measurement_sessions.id"), primary_key=True
        ),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("ai_products.id"), nullable=False),
        sa.Column("product_label", sa.String(200), nullable=False),
        sa.Column("measurement_kind", measurement_kind_enum, nullable=False),
        sa.Column("is_trial", sa.Boolean(), nullable=False),
        sa.Column("markers", _JSONB, nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "report_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("score_split", score_split_enum, nullable=False),
        sa.Column("cases_graded_count", sa.Integer(), nullable=False),
        sa.Column("cases_basis_count", sa.Integer(), nullable=False),
        sa.Column("det_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("llm_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("failure_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("rank", sa.SmallInteger(), nullable=True),
        sa.Column("repetitions", sa.SmallInteger(), nullable=False),
        sa.Column("product_model_version", sa.String(200), nullable=True),
        sa.Column("judge_model_version", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_id", "session_id"],
            ["report_sessions.report_id", "report_sessions.session_id"],
            name="fk_report_results_report_session",
        ),
    )
    op.create_table(
        "report_case_basis",
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id"), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.id"), primary_key=True),
        sa.Column(
            "case_version_id", sa.Uuid(), sa.ForeignKey("case_versions.id"), nullable=False
        ),
        sa.Column("split_tag", split_tag_enum, nullable=False),
    )
    op.create_table(
        "published_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "supersedes_id", sa.Uuid(), sa.ForeignKey("published_reports.id"), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_table("published_reports")
    op.drop_table("report_case_basis")
    op.drop_table("report_results")
    op.drop_table("report_sessions")
    op.drop_table("reports")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in reversed(_ENUMS):
            enum.drop(bind, checkfirst=True)
