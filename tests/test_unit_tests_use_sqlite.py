"""Unit tests must never reach a real database through the app's own settings.

Every TestClient start runs the app lifespan, and the lifespan runs session
recovery (``_recover_sessions_on_startup``) against ``get_settings().database_url``.
Without a pin in conftest that URL comes from ``.env`` or the default, which is
a real Postgres. Found by profiling (IR QA notes, 12 Nov): on a laptop with that
Postgres stopped every app start waited 4 x 6 s, and with it running the unit
tests ran session recovery against the developer's real database.
"""

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.main import app
from app.shared.config import get_settings


def test_app_settings_point_at_sqlite_during_tests():
    assert get_settings().database_url.startswith("sqlite")


def test_app_startup_only_connects_to_sqlite():
    """Edge: the connect is hidden in the startup hook, not in any test body.

    Edge because no test asks for that connection, so no test fails when it
    goes to the wrong database. If missed, a stopped Postgres only makes the
    suite slow (the hook swallows the error), and a running one gets real
    recovery writes. ``do_connect`` fires before the attempt, so this also
    catches a connect that would fail.
    """
    dialects: list[str] = []

    def record(dialect, _conn_rec, _cargs, _cparams):
        dialects.append(dialect.name)

    event.listen(Engine, "do_connect", record)
    try:
        with TestClient(app):
            pass
    finally:
        event.remove(Engine, "do_connect", record)

    assert all(name == "sqlite" for name in dialects), dialects
