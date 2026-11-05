"""PBI-12 (SCRUM-217): allow running -> pending attempt transition for restart recovery.

Extends the attempts_guard_status trigger function from e7a41c2b9d53 to allow
interrupted attempts to be reset from running to pending when the server or worker
restarts after a crash (AC8, D5).

Revision ID: 63745ac5e515
Revises: e7a41c2b9d53
"""

from alembic import op

revision = "63745ac5e515"  # pragma: allowlist secret
down_revision = "e7a41c2b9d53"  # pragma: allowlist secret
branch_labels = None
depends_on = None

ALLOWED = (
    ("pending", "running"),
    ("running", "succeeded"),
    ("running", "technical_failure"),
    ("running", "pending"),
    ("technical_failure", "pending"),
)

_PREVIOUS_ALLOWED = (
    ("pending", "running"),
    ("running", "succeeded"),
    ("running", "technical_failure"),
    ("technical_failure", "pending"),
)

_PAIRS = ", ".join(f"('{old}', '{new}')" for old, new in ALLOWED)
_PREVIOUS_PAIRS = ", ".join(f"('{old}', '{new}')" for old, new in _PREVIOUS_ALLOWED)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION attempts_guard_status() RETURNS trigger AS $$
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


def downgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION attempts_guard_status() RETURNS trigger AS $$
        BEGIN
            IF (OLD.status::text, NEW.status::text) NOT IN ({_PREVIOUS_PAIRS}) THEN
                RAISE EXCEPTION 'attempt status cannot move from % to %',
                    OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
