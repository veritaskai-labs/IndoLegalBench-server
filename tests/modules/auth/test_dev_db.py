"""Local SQLite stub for SCRUM-89 (create_all + DEV_ZITADEL_SUB)."""

from app.modules.auth.models import User
from app.modules.auth.seeds import AUTHOR_ID, AUTHOR_SUB, apply_dev_zitadel_sub, seed_users
from app.shared.config import get_settings
from app.shared.database import get_engine, get_session_factory
from app.shared.dev_db import bootstrap_local_sqlite


def test_apply_dev_zitadel_sub_updates_author_after_seed(db_session):
    seed_users(db_session)
    apply_dev_zitadel_sub(db_session, "277123456789")
    user = db_session.get(User, AUTHOR_ID)
    assert user is not None
    assert user.zitadel_sub == "277123456789"


def test_apply_dev_zitadel_sub_empty_leaves_seed(db_session):
    seed_users(db_session)
    apply_dev_zitadel_sub(db_session, "")
    user = db_session.get(User, AUTHOR_ID)
    assert user is not None
    assert user.zitadel_sub == AUTHOR_SUB


def test_bootstrap_skips_non_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    bootstrap_local_sqlite()
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


def test_bootstrap_sqlite_seeds_and_overrides_sub(tmp_path, monkeypatch):
    db_file = tmp_path / "local.db"
    url = f"sqlite:///{db_file.resolve().as_posix()}"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DEV_ZITADEL_SUB", "277999888777")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    try:
        bootstrap_local_sqlite()
        db = get_session_factory()()
        try:
            user = db.get(User, AUTHOR_ID)
            assert user is not None
            assert user.zitadel_sub == "277999888777"
        finally:
            db.close()
        bootstrap_local_sqlite()
        db = get_session_factory()()
        try:
            user = db.get(User, AUTHOR_ID)
            assert user is not None
            assert user.zitadel_sub == "277999888777"
        finally:
            db.close()
    finally:
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_session_factory.cache_clear()
