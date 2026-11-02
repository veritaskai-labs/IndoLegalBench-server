"""Retire the manual suite snapshots of Sprint 2.

SCRUM-207, PBI-11 AC7: the 'Buat snapshot' button and the snapshot list are
gone from the Suite page, and "old snapshots made by hand while testing may
be deleted". D2 retires suite_snapshots; snapshots are now taken
automatically when a measurement session starts.

The ticket asks for a step that can be rolled back, so the rows are not
dropped: upgrade copies them into retired_suite_snapshots and
retired_suite_snapshot_items and then empties the live tables. The downgrade
copies them back and drops the retired_* tables. The live tables keep their
schema, because the audit tracker and past suite.snapshot_created rows in
audit_logs still refer to them. Raw SQL does not pass the ORM audit
listener, so this cleanup leaves no audit rows of its own.

Revision ID: e4b7c2d9a615
Revises: d3c8a1e54b20
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b7c2d9a615"  # pragma: allowlist secret
down_revision = "d3c8a1e54b20"  # pragma: allowlist secret
branch_labels = None
depends_on = None

_SNAPSHOT_COLUMNS = "id, suite_id, created_by, created_at"
_ITEM_COLUMNS = "id, snapshot_id, case_id, case_version_id, body"


def retire_manual_snapshots(bind: sa.Connection) -> int:
    """Move every manual snapshot into retired_* tables. Returns the snapshots moved."""
    bind.execute(
        sa.text(
            f"CREATE TABLE retired_suite_snapshots AS "
            f"SELECT {_SNAPSHOT_COLUMNS} FROM suite_snapshots"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE TABLE retired_suite_snapshot_items AS "
            f"SELECT {_ITEM_COLUMNS} FROM suite_snapshot_items"
        )
    )
    # Items first: suite_snapshot_items.snapshot_id refers to suite_snapshots.
    bind.execute(sa.text("DELETE FROM suite_snapshot_items"))
    return bind.execute(sa.text("DELETE FROM suite_snapshots")).rowcount


def restore_manual_snapshots(bind: sa.Connection) -> int:
    """Put the retired snapshots back and drop retired_*. Returns the snapshots restored."""
    restored = bind.execute(
        sa.text(
            f"INSERT INTO suite_snapshots ({_SNAPSHOT_COLUMNS}) "
            f"SELECT {_SNAPSHOT_COLUMNS} FROM retired_suite_snapshots"
        )
    ).rowcount
    bind.execute(
        sa.text(
            f"INSERT INTO suite_snapshot_items ({_ITEM_COLUMNS}) "
            f"SELECT {_ITEM_COLUMNS} FROM retired_suite_snapshot_items"
        )
    )
    bind.execute(sa.text("DROP TABLE retired_suite_snapshot_items"))
    bind.execute(sa.text("DROP TABLE retired_suite_snapshots"))
    return restored


def upgrade() -> None:
    retire_manual_snapshots(op.get_bind())


def downgrade() -> None:
    restore_manual_snapshots(op.get_bind())
