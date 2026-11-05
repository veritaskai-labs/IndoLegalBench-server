"""PBI-12 (SCRUM-214): attempt status moves follow D4 part B in the database.

Automatic attempts may only move pending -> running, running -> succeeded or
technical_failure, and technical_failure -> pending (manual retry). succeeded is final.
Several code paths write attempts.status (worker, retry, restart recovery), so the
database enforces the table for all of them; app.modules.runs.attempt_transitions holds
the same table for the service layer.

The table is copied here, not imported, so a later change to the app cannot rewrite what
this revision did. tests/integration/test_attempt_status_guard_pg.py checks the copy
against the app table.

Only rows whose status actually changes are checked (a write that keeps the status, e.g.
setting model_version, passes). Manual attempts (PBI-22) are not covered yet.

The downgrade drops the trigger and its function; nothing else depends on them.

Revision ID: e7a41c2b9d53
Revises: 9b526a16b704
"""

from alembic import op

revision = "e7a41c2b9d53"  # pragma: allowlist secret
down_revision = "9b526a16b704"  # pragma: allowlist secret
branch_labels = None
depends_on = None

ALLOWED = (
    ("pending", "running"),
    ("running", "succeeded"),
    ("running", "technical_failure"),
    ("technical_failure", "pending"),
)

_PAIRS = ", ".join(f"('{old}', '{new}')" for old, new in ALLOWED)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION attempts_guard_status() RETURNS trigger AS $$
        BEGIN
            IF (OLD.status::text, NEW.status::text) NOT IN ({_PAIRS}) THEN
                RAISE EXCEPTION 'attempt status cannot move from % to %',
                    OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER attempts_guard_status
        BEFORE UPDATE OF status ON attempts
        FOR EACH ROW
        WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.source = 'automatic')
        EXECUTE FUNCTION attempts_guard_status()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER attempts_guard_status ON attempts")
    op.execute("DROP FUNCTION attempts_guard_status()")
