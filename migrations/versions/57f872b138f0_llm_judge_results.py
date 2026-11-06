"""LLM judge rows per repetition and kind, and their per-criterion results.

SCRUM-222, PBI-14 AC3, AC4, AC5, AC6, D6, with the decisions of 6 Nov 2026.

The shape follows #108 (JudgeRow): one llm_judge_results row per
(judge_run_no, kind quality/format), each with its own prompt, criteria,
score, error and cost (PO decision K3). criteria_score 0-1 is the "skor LLM"
of the result table; rubric_level 0-5 is kept apart and only on quality rows
(K1/K2). A failed call keeps its tokens and cost (separate judge budget, AC6)
but has no score. There is no trap kind (client 27 Oct, AC4). The exchange
rate is stored as usd_idr_rate like call_logs, not as an fx_rates FK.

llm_criterion_results holds one row per answer criterion of the snapshot.
The placeholder reason "unmet" of #108 is replaced by catalog codes: a reason
only on an unmet criterion and only an active LLM-path code. The row-level
reason of JudgeRow is not stored, because it is derived from these rows.

Both tables are INSERT-only, only into an open run; a technical-disruption
result (AC9) and a failed call get no judge or criterion rows. The downgrade
is refused while any judge row exists.

Revision ID: 57f872b138f0
Revises: 8ead8669ff79
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.shared.text import sql_not_blank

revision = "57f872b138f0"  # pragma: allowlist secret
down_revision = "8ead8669ff79"  # pragma: allowlist secret
branch_labels = None
depends_on = None

judge_kind_enum = postgresql.ENUM(
    "quality", "format", name="llm_judge_kind_enum", create_type=False
)
judge_status_enum = postgresql.ENUM(
    "succeeded", "failed", name="llm_judge_status_enum", create_type=False
)

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

JUDGE_STATUS_CHECK = (
    "(status = 'succeeded' AND criteria_met IS NOT NULL AND criteria_total IS NOT NULL "
    "AND criteria_score IS NOT NULL AND failure_kind IS NULL AND error IS NULL) OR "
    "(status = 'failed' AND failure_kind IS NOT NULL AND criteria_met IS NULL "
    "AND criteria_total IS NULL AND criteria_score IS NULL AND rubric_level IS NULL)"
)

_GUARD = """
    CREATE FUNCTION llm_judge_guard() RETURNS trigger AS $$
    DECLARE
        result_id uuid;
        judge_status text;
        is_disruption boolean;
        run_status text;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION '% is insert-only, grade again with a new run', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_TABLE_NAME = 'llm_criterion_results' THEN
            SELECT grading_result_id, status::text INTO result_id, judge_status
                FROM llm_judge_results WHERE id = NEW.llm_judge_result_id;
            IF judge_status = 'failed' THEN
                RAISE EXCEPTION 'a failed judge call has no criterion results'
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM 1 FROM reason_codes
                WHERE code = NEW.reason_code AND (NOT is_active OR grader_path <> 'llm');
            IF FOUND THEN
                RAISE EXCEPTION 'reason code % is not an active LLM reason', NEW.reason_code
                    USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            result_id := NEW.grading_result_id;
        END IF;
        SELECT g.is_technical_disruption, r.status::text INTO is_disruption, run_status
            FROM grading_results g JOIN grading_runs r ON r.id = g.grading_run_id
            WHERE g.id = result_id
            FOR SHARE OF r;
        IF is_disruption THEN
            RAISE EXCEPTION 'a technical-disruption attempt has no answer to judge'
                USING ERRCODE = 'check_violation';
        END IF;
        IF run_status IN ('finished', 'failed') THEN
            RAISE EXCEPTION 'grading run is closed'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""

_TABLES = ("llm_judge_results", "llm_criterion_results")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        judge_kind_enum.create(bind, checkfirst=True)
        judge_status_enum.create(bind, checkfirst=True)

    def _enum(enum: postgresql.ENUM) -> sa.types.TypeEngine:
        return enum if is_postgres else sa.String(32)

    op.create_table(
        "llm_judge_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "grading_result_id", sa.Uuid(), sa.ForeignKey("grading_results.id"), nullable=False
        ),
        sa.Column("judge_run_no", sa.SmallInteger(), nullable=False),
        sa.Column("kind", _enum(judge_kind_enum), nullable=False),
        sa.Column("status", _enum(judge_status_enum), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("criteria_met", sa.SmallInteger(), nullable=True),
        sa.Column("criteria_total", sa.SmallInteger(), nullable=True),
        sa.Column("criteria_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("rubric_level", sa.SmallInteger(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("failure_kind", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_idr", sa.Numeric(18, 2), nullable=True),
        sa.Column("usd_idr_rate", sa.Numeric(18, 4), nullable=True),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("raw_response", _JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "grading_result_id", "judge_run_no", "kind", name="uq_llm_judge_results_run_kind"
        ),
        sa.CheckConstraint(
            "judge_run_no BETWEEN 1 AND 5", name="ck_llm_judge_results_judge_run_no"
        ),
        sa.CheckConstraint(sql_not_blank("prompt"), name="ck_llm_judge_results_prompt"),
        sa.CheckConstraint(
            "criteria_met BETWEEN 0 AND criteria_total", name="ck_llm_judge_results_criteria"
        ),
        sa.CheckConstraint(
            "criteria_score IS NULL OR criteria_score BETWEEN 0 AND 1",
            name="ck_llm_judge_results_criteria_score",
        ),
        sa.CheckConstraint(
            "rubric_level IS NULL OR (rubric_level BETWEEN 0 AND 5 AND kind = 'quality')",
            name="ck_llm_judge_results_rubric_level",
        ),
        sa.CheckConstraint(JUDGE_STATUS_CHECK, name="ck_llm_judge_results_status"),
        sa.CheckConstraint(
            "coalesce(input_tokens, 0) >= 0 AND coalesce(output_tokens, 0) >= 0",
            name="ck_llm_judge_results_tokens",
        ),
        sa.CheckConstraint(
            "cost_idr IS NULL OR cost_idr >= 0", name="ck_llm_judge_results_cost"
        ),
        sa.CheckConstraint(
            "usd_idr_rate IS NULL OR usd_idr_rate > 0", name="ck_llm_judge_results_rate"
        ),
    )

    op.create_table(
        "llm_criterion_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "llm_judge_result_id",
            sa.Uuid(),
            sa.ForeignKey("llm_judge_results.id"),
            nullable=False,
        ),
        sa.Column("criterion_key", sa.String(length=64), nullable=False),
        sa.Column("criterion_text", sa.Text(), nullable=False),
        sa.Column("is_met", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "reason_code", sa.String(length=64), sa.ForeignKey("reason_codes.code"), nullable=True
        ),
        sa.UniqueConstraint(
            "llm_judge_result_id", "criterion_key", name="uq_llm_criterion_results_key"
        ),
        sa.CheckConstraint(
            sql_not_blank("criterion_text"), name="ck_llm_criterion_results_text"
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR NOT is_met", name="ck_llm_criterion_results_reason_unmet"
        ),
    )

    if is_postgres:
        op.execute(_GUARD)
        for table in _TABLES:
            op.execute(
                f"""
                CREATE TRIGGER {table}_guard
                BEFORE INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION llm_judge_guard()
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER {table}_block_truncate
                BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION llm_judge_guard()
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    jumlah = bind.execute(sa.text("SELECT count(*) FROM llm_judge_results")).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} LLM judge row(s) exist. Export the judge results before removing "
            "the judge tables by hand."
        )

    op.drop_table("llm_criterion_results")
    op.drop_table("llm_judge_results")
    if is_postgres:
        op.execute("DROP FUNCTION llm_judge_guard()")
        judge_status_enum.drop(bind, checkfirst=True)
        judge_kind_enum.drop(bind, checkfirst=True)
