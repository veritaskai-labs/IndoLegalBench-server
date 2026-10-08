"""allow audit retention delete

Revision ID: c5e7a1d93f40
Revises: a9c3e5f71b28
Create Date: 2026-10-08 15:00:00.000000

PBI-18 AC7. Audit rows are kept 90 days, then deleted permanently by a
daily job (client decision). The job connects as ilb_retention, and the
append-only trigger lets that role, and only that role, delete rows older
than 90 days. UPDATE and TRUNCATE stay blocked for everyone.

The role itself is created by whoever runs the database (docs/DEPLOY.md).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5e7a1d93f40"  # pragma: allowlist secret
down_revision: str | None = "a9c3e5f71b28"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RAISE = """
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION audit_logs_forbid_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
                AND current_user = 'ilb_retention'
                AND OLD.occurred_at < now() - interval '90 days' THEN
                RETURN OLD;
            END IF;{_RAISE}
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ilb_retention') THEN
                GRANT SELECT, DELETE ON audit_logs TO ilb_retention;
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
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ilb_retention') THEN
                REVOKE ALL ON audit_logs FROM ilb_retention;
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION audit_logs_forbid_mutation() RETURNS trigger AS $$
        BEGIN{_RAISE}
        END;
        $$ LANGUAGE plpgsql
        """
    )
