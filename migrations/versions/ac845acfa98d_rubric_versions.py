"""Versioned rubric: versions, aspects, levels and level descriptors.

SCRUM-222, PBI-14 AC3 and AC7, D6, with the PO decisions of 6 Nov 2026: rubric
levels 0-5 are data with a passing flag, the rubric's dimensions are
rubric_aspects with nullable weights (all NULL, or all filled and totalling
100), and the approver is a free-text name plus a date, not a user account.

Every grading result will point to the version it was graded with, so a
version never changes once written. On PostgreSQL:
- a new version starts inactive and unlocked, so activation always goes through
  the UPDATE checks; the only allowed UPDATE is flipping is_active; DELETE and
  TRUNCATE are refused;
- activating a version needs at least one aspect and one level and a valid
  weight set, and stamps locked_at the first time;
- aspects, levels and descriptors are INSERT-only, and only into a version that
  has never been active. The insert trigger takes FOR SHARE on the version row
  without filtering on locked_at, so an INSERT waits for an uncommitted
  activation and then sees locked_at, and an activation waits for an
  uncommitted INSERT and then counts it;
- a descriptor's level and aspect must belong to the same version.
A partial unique index keeps one active version.

The downgrade drops everything, so it is refused while any rubric version
exists (same rule as c1fd2ffcc735: never lose rows silently).

Revision ID: ac845acfa98d
Revises: df7cc61c635a
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.shared.text import sql_not_blank

revision = "ac845acfa98d"  # pragma: allowlist secret
down_revision = "df7cc61c635a"  # pragma: allowlist secret
branch_labels = None
depends_on = None

grader_path_enum = postgresql.ENUM(
    "deterministic", "llm", name="grader_path_enum", create_type=False
)

_CONTENT_TABLES = ("rubric_aspects", "rubric_levels", "rubric_level_descriptors")

_GUARD_VERSION = """
    CREATE FUNCTION rubric_versions_guard_change() RETURNS trigger AS $$
    DECLARE
        aspect_count integer;
        weighted_count integer;
        weight_total numeric;
        level_count integer;
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
            RAISE EXCEPTION 'rubric versions cannot be deleted'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.is_active OR NEW.locked_at IS NOT NULL THEN
                RAISE EXCEPTION 'a new rubric version starts inactive; activate it with UPDATE'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.is_active = OLD.is_active
           OR (to_jsonb(NEW) - 'is_active' - 'locked_at')
              IS DISTINCT FROM (to_jsonb(OLD) - 'is_active' - 'locked_at')
           OR (OLD.locked_at IS NOT NULL AND NEW.locked_at IS DISTINCT FROM OLD.locked_at)
        THEN
            RAISE EXCEPTION 'rubric version % is immutable, create a new version', OLD.version_no
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.is_active THEN
            SELECT count(*), count(weight), coalesce(sum(weight), 0)
                INTO aspect_count, weighted_count, weight_total
                FROM rubric_aspects WHERE rubric_version_id = NEW.id;
            SELECT count(*) INTO level_count FROM rubric_levels WHERE rubric_version_id = NEW.id;
            IF aspect_count = 0 OR level_count = 0 THEN
                RAISE EXCEPTION 'rubric version % needs at least one aspect and one level',
                    NEW.version_no USING ERRCODE = 'check_violation';
            END IF;
            IF weighted_count > 0 AND (weighted_count < aspect_count OR weight_total <> 100) THEN
                RAISE EXCEPTION 'rubric version % weights must be all empty or total 100',
                    NEW.version_no USING ERRCODE = 'check_violation';
            END IF;
            NEW.locked_at := coalesce(OLD.locked_at, NEW.locked_at, now());
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""

_GUARD_CONTENT = """
    CREATE FUNCTION rubric_content_guard() RETURNS trigger AS $$
    DECLARE
        version_id uuid;
        aspect_version_id uuid;
        version_locked_at timestamptz;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION '% is insert-only, create a new rubric version', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_TABLE_NAME = 'rubric_level_descriptors' THEN
            SELECT rubric_version_id INTO version_id
                FROM rubric_levels WHERE id = NEW.rubric_level_id;
            SELECT rubric_version_id INTO aspect_version_id
                FROM rubric_aspects WHERE id = NEW.rubric_aspect_id;
            IF version_id IS DISTINCT FROM aspect_version_id THEN
                RAISE EXCEPTION 'descriptor level and aspect belong to different versions'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
        ELSE
            version_id := NEW.rubric_version_id;
        END IF;
        -- Lock the row whatever its state: a filter on locked_at would skip a row whose
        -- activation is still uncommitted, so neither side would wait for the other.
        SELECT locked_at INTO version_locked_at FROM rubric_versions WHERE id = version_id FOR SHARE;
        IF version_locked_at IS NOT NULL THEN
            RAISE EXCEPTION 'rubric version is locked, create a new version'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        grader_path_enum.create(bind, checkfirst=True)

    op.create_table(
        "rubric_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("judge_prompt_quality", sa.Text(), nullable=False),
        sa.Column("judge_prompt_format", sa.Text(), nullable=False),
        sa.Column("approved_by_name", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("version_no", name="rubric_versions_version_no_key"),
        sa.CheckConstraint("version_no >= 1", name="ck_rubric_versions_version_no"),
        sa.CheckConstraint(
            sql_not_blank("judge_prompt_quality"), name="ck_rubric_versions_prompt_quality"
        ),
        sa.CheckConstraint(
            sql_not_blank("judge_prompt_format"), name="ck_rubric_versions_prompt_format"
        ),
        sa.CheckConstraint(
            f"approved_by_name IS NULL OR {sql_not_blank('approved_by_name')}",
            name="ck_rubric_versions_approved_by_name",
        ),
        sa.CheckConstraint(
            "NOT is_active OR locked_at IS NOT NULL", name="ck_rubric_versions_active_locked"
        ),
    )
    op.create_index(
        "uq_rubric_versions_one_active",
        "rubric_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active"),
    )

    op.create_table(
        "rubric_aspects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rubric_version_id", sa.Uuid(), sa.ForeignKey("rubric_versions.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "grader_path",
            grader_path_enum if is_postgres else sa.String(32),
            nullable=False,
        ),
        sa.Column("weight", sa.Numeric(5, 2), nullable=True),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.UniqueConstraint("rubric_version_id", "name", name="uq_rubric_aspects_version_name"),
        sa.UniqueConstraint(
            "rubric_version_id", "sort_order", name="uq_rubric_aspects_version_order"
        ),
        sa.CheckConstraint(sql_not_blank("name"), name="ck_rubric_aspects_name"),
        sa.CheckConstraint(sql_not_blank("definition"), name="ck_rubric_aspects_definition"),
        sa.CheckConstraint(
            "weight IS NULL OR (weight > 0 AND weight <= 100)", name="ck_rubric_aspects_weight"
        ),
        sa.CheckConstraint("sort_order >= 1", name="ck_rubric_aspects_sort_order"),
    )

    op.create_table(
        "rubric_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rubric_version_id", sa.Uuid(), sa.ForeignKey("rubric_versions.id"), nullable=False
        ),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("is_passing", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("rubric_version_id", "score", name="uq_rubric_levels_version_score"),
        sa.CheckConstraint("score BETWEEN 0 AND 5", name="ck_rubric_levels_score"),
        sa.CheckConstraint(sql_not_blank("label"), name="ck_rubric_levels_label"),
    )

    op.create_table(
        "rubric_level_descriptors",
        sa.Column(
            "rubric_level_id",
            sa.Uuid(),
            sa.ForeignKey("rubric_levels.id"),
            primary_key=True,
        ),
        sa.Column(
            "rubric_aspect_id",
            sa.Uuid(),
            sa.ForeignKey("rubric_aspects.id"),
            primary_key=True,
        ),
        sa.Column("descriptor", sa.Text(), nullable=False),
        sa.CheckConstraint(sql_not_blank("descriptor"), name="ck_rubric_level_descriptors_text"),
    )

    if is_postgres:
        op.execute(_GUARD_VERSION)
        op.execute(
            """
            CREATE TRIGGER rubric_versions_guard_change
            BEFORE INSERT OR UPDATE OR DELETE ON rubric_versions
            FOR EACH ROW EXECUTE FUNCTION rubric_versions_guard_change()
            """
        )
        op.execute(
            """
            CREATE TRIGGER rubric_versions_block_truncate
            BEFORE TRUNCATE ON rubric_versions
            FOR EACH STATEMENT EXECUTE FUNCTION rubric_versions_guard_change()
            """
        )
        op.execute(_GUARD_CONTENT)
        for table in _CONTENT_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER {table}_guard
                BEFORE INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION rubric_content_guard()
                """
            )
            op.execute(
                f"""
                CREATE TRIGGER {table}_block_truncate
                BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION rubric_content_guard()
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    jumlah = bind.execute(sa.text("SELECT count(*) FROM rubric_versions")).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} rubric version(s) exist. Graded results depend on them; export them "
            "before removing the rubric tables by hand."
        )

    op.drop_table("rubric_level_descriptors")
    op.drop_table("rubric_levels")
    op.drop_table("rubric_aspects")
    op.drop_index("uq_rubric_versions_one_active", "rubric_versions")
    op.drop_table("rubric_versions")
    if is_postgres:
        op.execute("DROP FUNCTION rubric_content_guard()")
        op.execute("DROP FUNCTION rubric_versions_guard_change()")
        grader_path_enum.drop(bind, checkfirst=True)
