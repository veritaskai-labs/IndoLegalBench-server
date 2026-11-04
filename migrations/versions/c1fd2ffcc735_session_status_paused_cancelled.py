"""PBI-12 (SCRUM-214): paused and cancelled session statuses, and why a session stopped.

D4 (Astha's state diagram) adds two session statuses on top of the PBI-11 base:
paused (Jeda, or the system stopped the run) and cancelled (Batalkan). Names follow
the staging enum, not the D4 labels.

D2's stopped_at and stop_reason record when and why a session stopped (agreed with
Adra, 4 Nov): user, budget or restart for paused, error for failed. A CHECK keeps the
two together. They are cleared when the session runs again.

PostgreSQL 12+ allows ADD VALUE inside a transaction, as long as the new value is
not used in that same transaction. Nothing here uses it.

PostgreSQL cannot drop an enum value, so the downgrade rebuilds the type. It refuses
while a session is still paused or cancelled: mapping them to another status would
rewrite measured history. The status default, the running-not-archived CHECK and
the one-running-automatic index compare against the type, so they are dropped and
recreated around the swap.

Revision ID: c1fd2ffcc735
Revises: e4b7c2d9a615
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1fd2ffcc735"  # pragma: allowlist secret
down_revision = "e4b7c2d9a615"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_NEW_VALUES = ("paused", "cancelled")
_BASE_VALUES = ("queued", "running", "finished", "finished_with_failures", "failed")

stop_reason_enum = postgresql.ENUM(
    "user", "budget", "restart", "error", name="measurement_stop_reason_enum", create_type=False
)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for value in _NEW_VALUES:
        op.execute(f"ALTER TYPE measurement_session_status_enum ADD VALUE IF NOT EXISTS '{value}'")

    stop_reason_enum.create(bind, checkfirst=True)
    op.add_column(
        "measurement_sessions",
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("measurement_sessions", sa.Column("stop_reason", stop_reason_enum, nullable=True))
    op.create_check_constraint(
        "ck_measurement_sessions_stop_complete",
        "measurement_sessions",
        "(stopped_at IS NULL) = (stop_reason IS NULL)",
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    jumlah = bind.execute(
        sa.text("SELECT count(*) FROM measurement_sessions WHERE status IN ('paused', 'cancelled')")
    ).scalar()
    if jumlah:
        raise RuntimeError(
            f"{jumlah} measurement session(s) are still paused or cancelled. Resume and "
            "finish them, or move them by hand, before downgrading."
        )

    op.drop_constraint(
        "ck_measurement_sessions_stop_complete", "measurement_sessions", type_="check"
    )
    op.drop_column("measurement_sessions", "stop_reason")
    op.drop_column("measurement_sessions", "stopped_at")
    stop_reason_enum.drop(bind, checkfirst=True)

    op.drop_index("uq_measurement_sessions_one_running_automatic", "measurement_sessions")
    op.drop_constraint(
        "ck_measurement_sessions_running_not_archived", "measurement_sessions", type_="check"
    )
    op.execute("ALTER TABLE measurement_sessions ALTER COLUMN status DROP DEFAULT")

    op.execute(
        "ALTER TYPE measurement_session_status_enum RENAME TO measurement_session_status_enum_old"
    )
    values = ", ".join(f"'{value}'" for value in _BASE_VALUES)
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
