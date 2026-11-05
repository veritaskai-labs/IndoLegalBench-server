"""Number each call of an attempt in call_logs.

SCRUM-218, PBI-12 AC3: "Coba ulang yang gagal" adds a new call_logs row to the
same attempt instead of a new repetition. D2 call_logs has try_no (1 = the
first call) and is_manual_retry for that. D4 says there is no automatic retry,
so every call after the first comes from a user action.

Existing rows are numbered by started_at (then id) within their attempt_id, so
UNIQUE (attempt_id, try_no) can be added even if an attempt already has more
than one row. The correlated subquery runs on both PostgreSQL and SQLite. The
downgrade only drops the two columns; every call row stays.

Revision ID: df7cc61c635a
Revises: e7a41c2b9d53
"""

import sqlalchemy as sa
from alembic import op

revision = "df7cc61c635a"  # pragma: allowlist secret
down_revision = "e7a41c2b9d53"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_NUMBER_EXISTING_CALLS = """
    UPDATE call_logs
    SET try_no = (
        SELECT count(*) FROM call_logs AS earlier
        WHERE earlier.attempt_id = call_logs.attempt_id
          AND (
              earlier.started_at < call_logs.started_at
              OR (earlier.started_at = call_logs.started_at AND earlier.id <= call_logs.id)
          )
    )
"""


def upgrade():
    with op.batch_alter_table("call_logs") as batch:
        batch.add_column(
            sa.Column("try_no", sa.SmallInteger(), nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(
            sa.Column("is_manual_retry", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute(_NUMBER_EXISTING_CALLS)
    op.execute("UPDATE call_logs SET is_manual_retry = (try_no > 1)")
    with op.batch_alter_table("call_logs") as batch:
        batch.create_check_constraint("ck_call_logs_try_no", "try_no >= 1")
        batch.create_unique_constraint("uq_call_logs_attempt_try", ["attempt_id", "try_no"])


def downgrade():
    with op.batch_alter_table("call_logs") as batch:
        batch.drop_constraint("uq_call_logs_attempt_try", type_="unique")
        batch.drop_constraint("ck_call_logs_try_no", type_="check")
        batch.drop_column("is_manual_retry")
        batch.drop_column("try_no")
