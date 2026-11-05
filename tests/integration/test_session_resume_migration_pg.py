"""Real resume metadata defaults, evidence-preserving downgrade and DDL round trip."""

import importlib.util
import os
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.integration.test_session_status_migration_pg import conn
from tests.measurement_pg import buat_admin, buat_produk, buat_sesi

__all__ = ["conn"]
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL required"),
]


def migration(conn, direction):
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/e216a5b7c901_resume_guard_metadata.py"
    )
    spec = importlib.util.spec_from_file_location("resume_metadata_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()


def session(conn):
    admin = buat_admin(conn)
    return buat_sesi(conn, buat_produk(conn, admin), admin)


def test_defaults_and_round_trip_preserve_session_rows(conn):
    session_id = session(conn)
    values = conn.execute(
        text(
            "SELECT resumed_after_long_pause, model_version_unverified, "
            "resume_version_check_pending, resume_error_code FROM measurement_sessions WHERE id=:id"
        ),
        {"id": session_id},
    ).one()
    assert tuple(values) == (False, False, False, None)
    migration(conn, "downgrade")
    assert (
        conn.scalar(text("SELECT id FROM measurement_sessions WHERE id=:id"), {"id": session_id})
        == session_id
    )
    migration(conn, "upgrade")
    assert (
        conn.scalar(
            text("SELECT resumed_after_long_pause FROM measurement_sessions WHERE id=:id"),
            {"id": session_id},
        )
        is False
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "resumed_after_long_pause=true",
        "model_version_unverified=true",
        "resume_version_check_pending=true",
        "resume_error_code='MODEL_VERSION_CHANGED'",
    ],
)
def test_downgrade_refuses_to_discard_resume_evidence(conn, assignment):
    session_id = session(conn)
    conn.execute(
        text(f"UPDATE measurement_sessions SET {assignment} WHERE id=:id"), {"id": session_id}
    )
    with pytest.raises(RuntimeError, match="verification evidence"):
        migration(conn, "downgrade")
    assert (
        conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE "
                "table_name='measurement_sessions' AND column_name='resume_error_code'"
            )
        )
        == 1
    )
