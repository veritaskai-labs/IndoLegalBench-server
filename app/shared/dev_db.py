"""Local SQLite bootstrap while SCRUM-89 has no Alembic.

# TODO(SCRUM-89): remove SQLite create_all and DEV_ZITADEL_SUB; use Alembic + official seed
"""

from app.modules.auth.seeds import apply_dev_zitadel_sub, seed_users
from app.shared.config import get_settings
from app.shared.database import Base, get_engine, get_session_factory


def bootstrap_local_sqlite() -> None:
    settings = get_settings()
    if settings.app_env != "local":
        return
    if not settings.database_url.startswith("sqlite"):
        return

    # Register tables on Base.metadata (same imports Alembic uses).
    from app.modules.auth import models as auth_models  # noqa: F401
    from app.modules.suites import models as suites_models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    db = get_session_factory()()
    try:
        seed_users(db)
        apply_dev_zitadel_sub(db, settings.dev_zitadel_sub)
    finally:
        db.close()
