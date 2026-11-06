"""Grading runs, per-attempt deterministic results and the reason catalog.

SCRUM-222, PBI-14 AC1, AC2, AC5, AC7, AC9, D6, with the decisions of 6 Nov 2026.

grading_runs: one "Nilai" or "Nilai ulang" of a session. It pins the rubric
version it graded with (Adra's condition), so a new rubric never changes old
results. The version must have been activated, because a draft can still gain
aspects. The first run of a session is initial, later ones are regrade; an
advisory lock per session keeps two concurrent first runs from both passing.
A re-grade never deletes the old run, only is_latest moves (client No. 68 A,
one latest run per session). Only status, finished_at and is_latest may
change; status goes queued -> running -> finished/failed (queued -> failed for
a run that never starts) and a finished or failed run is closed.
judge_provider_id stays a uuid without FK until SCRUM-258 adds judge_providers.

grading_results: one row per attempt per run with every CitationScore field
of #107. det_score is NULL when no reference can be graded (SA note). There is
no combined score column (PO decision K7, AC1). A technical-disruption row
(AC9) carries no deterministic component. Rows are INSERT-only, only into a
run that is not closed, and the attempt must belong to the run's session.

reason_codes / grading_result_reasons (AC5): the client has not sent its list
yet, so four neutral codes are seeded with is_proposed = true. A link is
INSERT-only and needs an active code.

The downgrade is refused while any grading run exists.

Revision ID: 8ead8669ff79
Revises: ac845acfa98d
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.shared.text import sql_not_blank

revision = "8ead8669ff79"  # pragma: allowlist secret
down_revision = "ac845acfa98d"  # pragma: allowlist secret
branch_labels = None
depends_on = None

run_kind_enum = postgresql.ENUM(
    "initial", "regrade", name="grading_run_kind_enum", create_type=False
)
run_status_enum = postgresql.ENUM(
    "queued", "running", "finished", "failed", name="grading_run_status_enum", create_type=False
)
grader_path_enum = postgresql.ENUM(
    "deterministic", "llm", name="grader_path_enum", create_type=False
)

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

_DET_COLUMNS = (
    "det_score",
    "det_regulation_matches",
    "det_article_matches",
    "det_ungradable_refs",
    "det_all_chained_cited",
    "det_reference_total",
    "det_reference_correct",
    "det_extracted_total",
    "det_extracted_correct",
    "det_formula",
)
_DET_REQUIRED = (
    "det_regulation_matches",
    "det_article_matches",
    "det_ungradable_refs",
    "det_reference_total",
    "det_reference_correct",
    "det_extracted_total",
    "det_extracted_correct",
    "det_formula",
)
DET_COMPONENTS_CHECK = (
    "(is_technical_disruption AND "
    + " AND ".join(f"{c} IS NULL" for c in _DET_COLUMNS)
    + ") OR (NOT is_technical_disruption AND "
    + " AND ".join(f"{c} IS NOT NULL" for c in _DET_REQUIRED)
    + ")"
)

_SEED_REASONS = (
    ("regulation_not_cited", "deterministic", "Peraturan tidak disebut"),
    ("article_not_cited", "deterministic", "Pasal tidak disebut"),
    ("chained_refs_incomplete", "deterministic", "Rujukan berangkai kurang"),
    ("criterion_unmet", "llm", "Kriteria tidak terpenuhi"),
)

_GUARD_RUN = """
    CREATE FUNCTION grading_runs_guard() RETURNS trigger AS $$
    DECLARE
        version_locked_at timestamptz;
        has_earlier boolean;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'grading runs cannot be deleted'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_OP = 'INSERT' THEN
            SELECT locked_at INTO version_locked_at
                FROM rubric_versions WHERE id = NEW.rubric_version_id;
            IF FOUND AND version_locked_at IS NULL THEN
                RAISE EXCEPTION 'rubric version must be activated before grading'
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.session_id::text, 222));
            SELECT EXISTS (SELECT 1 FROM grading_runs WHERE session_id = NEW.session_id)
                INTO has_earlier;
            IF has_earlier <> (NEW.kind = 'regrade') THEN
                RAISE EXCEPTION 'the first grading run of a session is initial, later ones regrade'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - 'status' - 'finished_at' - 'is_latest')
           IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'finished_at' - 'is_latest')
        THEN
            RAISE EXCEPTION 'grading run identity cannot change'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.status IN ('finished', 'failed')
           AND (NEW.status <> OLD.status OR NEW.finished_at IS DISTINCT FROM OLD.finished_at)
        THEN
            RAISE EXCEPTION 'grading run is closed'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'queued' AND NEW.status IN ('running', 'failed'))
            OR (OLD.status = 'running' AND NEW.status IN ('finished', 'failed'))
        ) THEN
            RAISE EXCEPTION 'grading run cannot move from % to %', OLD.status, NEW.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""

_GUARD_RESULT = """
    CREATE FUNCTION grading_results_guard() RETURNS trigger AS $$
    DECLARE
        run_session_id uuid;
        run_status text;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION '% is insert-only, grade again with a new run', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_TABLE_NAME = 'grading_result_reasons' THEN
            PERFORM 1 FROM reason_codes WHERE code = NEW.reason_code AND NOT is_active;
            IF FOUND THEN
                RAISE EXCEPTION 'reason code % is inactive', NEW.reason_code
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT r.status::text INTO run_status
                FROM grading_results g JOIN grading_runs r ON r.id = g.grading_run_id
                WHERE g.id = NEW.grading_result_id
                FOR SHARE OF r;
        ELSE
            SELECT session_id, status::text INTO run_session_id, run_status
                FROM grading_runs WHERE id = NEW.grading_run_id FOR SHARE;
            PERFORM 1 FROM attempts
                WHERE id = NEW.attempt_id AND session_id IS DISTINCT FROM run_session_id;
            IF FOUND THEN
                RAISE EXCEPTION 'attempt % does not belong to the session of run %',
                    NEW.attempt_id, NEW.grading_run_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
        END IF;
        IF run_status IN ('finished', 'failed') THEN
            RAISE EXCEPTION 'grading run is closed'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""

_INSERT_ONLY_TABLES = ("grading_results", "grading_result_reasons")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        run_kind_enum.create(bind, checkfirst=True)
        run_status_enum.create(bind, checkfirst=True)

    def _enum(enum: postgresql.ENUM) -> sa.types.TypeEngine:
        return enum if is_postgres else sa.String(32)

    op.create_table(
        "grading_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("measurement_sessions.id"), nullable=False
        ),
        sa.Column(
            "rubric_version_id", sa.Uuid(), sa.ForeignKey("rubric_versions.id"), nullable=False
        ),
        # Belum ber-FK: tabel judge_providers datang di SCRUM-258.
        sa.Column("judge_provider_id", sa.Uuid(), nullable=True),
        sa.Column("kind", _enum(run_kind_enum), nullable=False),
        sa.Column("status", _enum(run_status_enum), server_default="queued", nullable=False),
        sa.Column("is_latest", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("triggered_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status IN ('finished', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_grading_runs_finished_at",
        ),
    )
    op.create_index("ix_grading_runs_session_id", "grading_runs", ["session_id"])
    op.create_index(
        "uq_grading_runs_one_latest",
        "grading_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("is_latest"),
        sqlite_where=sa.text("is_latest"),
    )

    op.create_table(
        "grading_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("grading_run_id", sa.Uuid(), sa.ForeignKey("grading_runs.id"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column(
            "is_technical_disruption", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("det_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("det_regulation_matches", _JSONB, nullable=True),
        sa.Column("det_article_matches", _JSONB, nullable=True),
        sa.Column("det_ungradable_refs", _JSONB, nullable=True),
        sa.Column("det_all_chained_cited", sa.Boolean(), nullable=True),
        sa.Column("det_reference_total", sa.Integer(), nullable=True),
        sa.Column("det_reference_correct", sa.Integer(), nullable=True),
        sa.Column("det_extracted_total", sa.Integer(), nullable=True),
        sa.Column("det_extracted_correct", sa.Integer(), nullable=True),
        sa.Column("det_formula", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("grading_run_id", "attempt_id", name="uq_grading_results_run_attempt"),
        sa.CheckConstraint(
            "det_score IS NULL OR det_score BETWEEN 0 AND 1", name="ck_grading_results_det_score"
        ),
        sa.CheckConstraint(
            "det_formula IS NULL OR det_formula IN ('recall', 'precision')",
            name="ck_grading_results_det_formula",
        ),
        sa.CheckConstraint(
            "det_reference_correct BETWEEN 0 AND det_reference_total "
            "AND det_extracted_correct BETWEEN 0 AND det_extracted_total",
            name="ck_grading_results_det_counts",
        ),
        sa.CheckConstraint(DET_COMPONENTS_CHECK, name="ck_grading_results_det_components"),
    )
    op.create_index("ix_grading_results_attempt_id", "grading_results", ["attempt_id"])

    reason_codes = op.create_table(
        "reason_codes",
        sa.Column("code", sa.String(length=64), primary_key=True),
        sa.Column(
            "grader_path",
            grader_path_enum if is_postgres else sa.String(32),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_proposed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(sql_not_blank("label"), name="ck_reason_codes_label"),
    )
    op.bulk_insert(
        reason_codes,
        [
            {"code": code, "grader_path": path, "label": label, "is_proposed": True}
            for code, path, label in _SEED_REASONS
        ],
    )

    op.create_table(
        "grading_result_reasons",
        sa.Column(
            "grading_result_id",
            sa.Uuid(),
            sa.ForeignKey("grading_results.id"),
            primary_key=True,
        ),
        sa.Column(
            "reason_code",
            sa.String(length=64),
            sa.ForeignKey("reason_codes.code"),
            primary_key=True,
        ),
    )

    if is_postgres:
        op.execute(_GUARD_RUN)
        op.execute(
            """
            CREATE TRIGGER grading_runs_guard
            BEFORE INSERT OR UPDATE OR DELETE ON grading_runs
            FOR EACH ROW EXECUTE FUNCTION grading_runs_guard()
            """
        )
        op.execute(
            """
            CREATE TRIGGER grading_runs_block_truncate
            BEFORE TRUNCATE ON grading_runs
            FOR EACH STATEMENT EXECUTE FUNCTION grading_runs_guard()
            """
        )
        op.execute(_GUARD_RESULT)
        for table in _INSERT_ONLY_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER {table}_guard
                BEFORE INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION grading_results_guard()
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER {table}_block_truncate
                BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION grading_results_guard()
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    jumlah = bind.execute(sa.text("SELECT count(*) FROM grading_runs")).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} grading run(s) exist. Export the grading results before removing "
            "the grading tables by hand."
        )

    op.drop_table("grading_result_reasons")
    op.drop_table("reason_codes")
    op.drop_index("ix_grading_results_attempt_id", "grading_results")
    op.drop_table("grading_results")
    op.drop_index("uq_grading_runs_one_latest", "grading_runs")
    op.drop_index("ix_grading_runs_session_id", "grading_runs")
    op.drop_table("grading_runs")
    if is_postgres:
        op.execute("DROP FUNCTION grading_results_guard()")
        op.execute("DROP FUNCTION grading_runs_guard()")
        run_status_enum.drop(bind, checkfirst=True)
        run_kind_enum.drop(bind, checkfirst=True)
