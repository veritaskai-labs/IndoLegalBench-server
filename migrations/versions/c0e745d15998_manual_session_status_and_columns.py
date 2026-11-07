"""PBI-22 (SCRUM-230): manual session statuses (D8) and the D9 session and attempt columns.

D8 (manual session state diagram) adds five statuses to the same session status type,
after the seven D4 ones: not_filled, partially_filled, complete, graded, finalized.
D9 (manual answer ERD) adds to the D2 tables:

- measurement_sessions.answer_date_from / answer_date_to: the range of dates answers were
  taken from the platform (AC13, PBI-15 report).
- measurement_sessions.finalized_at / finalized_by: who marked the session Selesai (AC8).
  A CHECK keeps the two together.
- attempts.filled_at: a manual attempt is filled once this is set, so the D8 status is
  counted from it. attempts.answered_at_platform: when the answer was taken (AC7).
  The other manual answer columns of D9 belong to SCRUM-231.

There is no CHECK tying statuses to session_type: existing rows and tests hold manual
sessions with D4 statuses, so the service keeps manual sessions on D8 statuses.

PostgreSQL 12+ allows ADD VALUE inside a transaction, as long as the new value is not used
in that same transaction. Nothing here uses it.

PostgreSQL cannot drop an enum value, so the downgrade rebuilds the type, like
c1fd2ffcc735. It refuses while a session still has a D8 status: mapping it to a D4 status
would rewrite measured history. The status default, the running-not-archived CHECK and
the one-running-automatic index compare against the type, so they are dropped and
recreated around the swap.

Revision ID: c0e745d15998
Revises: 57f872b138f0
"""

import sqlalchemy as sa
from alembic import op

revision = "c0e745d15998"  # pragma: allowlist secret
down_revision = "57f872b138f0"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_D4_VALUES = (
    "queued",
    "running",
    "finished",
    "finished_with_failures",
    "failed",
    "paused",
    "cancelled",
)
_D8_VALUES = ("not_filled", "partially_filled", "complete", "graded", "finalized")
_TS = sa.DateTime(timezone=True)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in _D8_VALUES:
            op.execute(
                f"ALTER TYPE measurement_session_status_enum ADD VALUE IF NOT EXISTS '{value}'"
            )

    op.add_column("measurement_sessions", sa.Column("answer_date_from", _TS, nullable=True))
    op.add_column("measurement_sessions", sa.Column("answer_date_to", _TS, nullable=True))
    op.add_column("measurement_sessions", sa.Column("finalized_at", _TS, nullable=True))
    op.add_column(
        "measurement_sessions",
        sa.Column("finalized_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
    )
    with op.batch_alter_table("measurement_sessions") as batch:
        batch.create_check_constraint(
            "ck_measurement_sessions_finalized_complete",
            "(finalized_at IS NULL) = (finalized_by IS NULL)",
        )
    op.add_column("attempts", sa.Column("filled_at", _TS, nullable=True))
    op.add_column("attempts", sa.Column("answered_at_platform", _TS, nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        values = ", ".join(f"'{value}'" for value in _D8_VALUES)
        jumlah = bind.execute(
            sa.text(f"SELECT count(*) FROM measurement_sessions WHERE status::text IN ({values})")
        ).scalar()
        if jumlah:
            raise RuntimeError(
                f"{jumlah} measurement session(s) still have a manual (D8) status. Move them "
                "by hand before downgrading."
            )

    op.drop_column("attempts", "answered_at_platform")
    op.drop_column("attempts", "filled_at")
    with op.batch_alter_table("measurement_sessions") as batch:
        batch.drop_constraint("ck_measurement_sessions_finalized_complete", type_="check")
        batch.drop_column("finalized_by")
        batch.drop_column("finalized_at")
        batch.drop_column("answer_date_to")
        batch.drop_column("answer_date_from")

    if bind.dialect.name != "postgresql":
        return
    op.drop_index("uq_measurement_sessions_one_running_automatic", "measurement_sessions")
    op.drop_constraint(
        "ck_measurement_sessions_running_not_archived", "measurement_sessions", type_="check"
    )
    op.execute("ALTER TABLE measurement_sessions ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TYPE measurement_session_status_enum RENAME TO measurement_session_status_enum_old"
    )
    values = ", ".join(f"'{value}'" for value in _D4_VALUES)
    op.execute(f"CREATE TYPE measurement_session_status_enum AS ENUM ({values})")
    op.execute(
        "ALTER TABLE measurement_sessions ALTER COLUMN status TYPE measurement_session_status_enum "
        "USING status::text::measurement_session_status_enum"
    )
    op.execute("DROP TYPE measurement_session_status_enum_old")
    op.execute("ALTER TABLE measurement_sessions ALTER COLUMN status SET DEFAULT 'queued'")
    op.create_check_constraint(
        "ck_measurement_sessions_running_not_archived",
        "measurement_sessions",
        "NOT (status = 'running' AND archived_at IS NOT NULL)",
    )
    op.create_index(
        "uq_measurement_sessions_one_running_automatic",
        "measurement_sessions",
        ["session_type"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND session_type = 'automatic'"),
    )
