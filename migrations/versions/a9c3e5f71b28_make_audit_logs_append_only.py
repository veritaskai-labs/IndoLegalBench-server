"""make audit_logs append-only

Revision ID: a9c3e5f71b28
Revises: f2b6d8a31c57
Create Date: 2026-10-08 14:00:00.000000

PBI-18 AC2, diagram D6c. Two layers that hold even if the application
has a bug:

- Trigger: rejects UPDATE, DELETE and TRUNCATE for every role, the table
  owner included. TRUNCATE gets its own statement-level trigger because
  row triggers never see it.
- Grants: ilb_app (the runtime role) may only SELECT and INSERT. Applied
  only when that role exists. Until then the API connects as the owner,
  which grants cannot restrict, and the trigger is the guard.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9c3e5f71b28"  # pragma: allowlist secret
down_revision: str | None = "f2b6d8a31c57"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION audit_logs_forbid_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_block_mutation
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_forbid_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_block_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_forbid_mutation()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ilb_app') THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM ilb_app;
                GRANT SELECT, INSERT ON audit_logs TO ilb_app;
                GRANT USAGE ON SEQUENCE audit_logs_id_seq TO ilb_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ilb_app') THEN
                REVOKE ALL ON audit_logs FROM ilb_app;
                REVOKE ALL ON SEQUENCE audit_logs_id_seq FROM ilb_app;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER audit_logs_block_truncate ON audit_logs")
    op.execute("DROP TRIGGER audit_logs_block_mutation ON audit_logs")
    op.execute("DROP FUNCTION audit_logs_forbid_mutation()")
