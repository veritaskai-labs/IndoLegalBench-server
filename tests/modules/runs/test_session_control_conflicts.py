"""Only the named one-running invariant is translated to a client conflict."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.runs.session_control_repository import is_active_slot_conflict


@pytest.mark.parametrize(
    "state,constraint,expected",
    [
        ("23505", "uq_measurement_sessions_one_running_automatic", True),
        ("23505", "unrelated_unique", False),
        ("23514", "uq_measurement_sessions_one_running_automatic", False),
        (None, None, False),
    ],
)
def test_only_exact_postgres_constraint_is_a_slot_conflict(state, constraint, expected):
    error = IntegrityError(
        "statement",
        {},
        SimpleNamespace(sqlstate=state, diag=SimpleNamespace(constraint_name=constraint)),
    )
    assert is_active_slot_conflict(error) is expected


@pytest.mark.parametrize("acquired,active", [(False, True), (True, False)])
def test_dispatcher_probe_on_postgres_reads_the_worker_lease(monkeypatch, acquired, active):
    """SCRUM-268: a held lease means the running attempt may still be in flight, so keep it."""
    from contextlib import contextmanager

    from app.modules.runs import session_control

    @contextmanager
    def lease(_bind):
        yield acquired

    monkeypatch.setattr(session_control, "worker_lease", lease)
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )
    assert session_control._dispatcher_is_active(db) is active
