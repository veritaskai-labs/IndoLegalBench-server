"""allow the split tag of an approved version to change, add cases.was_dev

Revision ID: b7e2d4f81c36
Revises: d4f7a2c18b36
Create Date: 2026-10-16 10:00:00.000000

PBI-9 AC3, SCRUM-164. D4a rule 1: an approved row stays immutable except
split_tag, which an Admin may change (who may change it is checked in
cases.service). updated_at moves with every update, so it is allowed too.

cases.was_dev records that a case once sat in the dev set. It is set when
an Admin moves an approved case from dev to test and is never cleared.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d4f81c36"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "d4f7a2c18b36"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONLY_TAG = """
CREATE OR REPLACE FUNCTION case_versions_reject_approved_mutation()
RETURNS trigger AS $$
BEGIN
    IF OLD.status::text = 'approved' THEN
        IF TG_OP = 'DELETE' OR (
            NEW.id, NEW.case_id, NEW.version_no, NEW.status, NEW.case_code,
            NEW.content, NEW.created_by, NEW.created_at, NEW.based_on_version_id
        ) IS DISTINCT FROM (
            OLD.id, OLD.case_id, OLD.version_no, OLD.status, OLD.case_code,
            OLD.content, OLD.created_by, OLD.created_at, OLD.based_on_version_id
        ) THEN
            RAISE EXCEPTION 'approved case version is immutable';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_EVERYTHING_LOCKED = """
CREATE OR REPLACE FUNCTION case_versions_reject_approved_mutation()
RETURNS trigger AS $$
BEGIN
    IF OLD.status::text = 'approved' THEN
        RAISE EXCEPTION 'approved case version is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column("was_dev", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(_ONLY_TAG)


def downgrade() -> None:
    op.execute(_EVERYTHING_LOCKED)
    op.drop_column("cases", "was_dev")
