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
    recovery writes.

    Two events, because each one alone misses a case:
    - ``do_connect`` fires before a new DBAPI connect, so it sees a connect
      that fails (Postgres stopped), which never reaches ``engine_connect``.
    - ``engine_connect`` fires on every checkout, so it sees a pooled
      connection reused from an earlier test (Postgres running, as in CI),
      which never reaches ``do_connect``.
    """
    dialects: list[str] = []

    def on_new_connect(dialect, _conn_rec, _cargs, _cparams):
        dialects.append(dialect.name)

    def on_checkout(conn):
        dialects.append(conn.dialect.name)

    event.listen(Engine, "do_connect", on_new_connect)
    event.listen(Engine, "engine_connect", on_checkout)
    try:
        with TestClient(app):
            pass
    finally:
        event.remove(Engine, "do_connect", on_new_connect)
        event.remove(Engine, "engine_connect", on_checkout)

    assert all(name == "sqlite" for name in dialects), dialects
