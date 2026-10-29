"""In-memory test users for PBI-1 (SQLite).

- Five active roles (Developer AI since SCRUM-183) + one deactivated; `zitadel_sub` used by fake login (`?sub=`)
- Not Alembic; production seed is SCRUM-89
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session as DbSession

from app.modules.auth.models import User
from app.shared.security import Role

AUTHOR_SUB = "111111111111111111"
REVIEWER_SUB = "222222222222222222"
ADMIN_SUB = "333333333333333333"
VIEWER_SUB = "444444444444444444"
DEACTIVATED_SUB = "555555555555555555"
DEVELOPER_AI_SUB = "666666666666666666"
UNKNOWN_SUB = "999999999999999999"

AUTHOR_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
REVIEWER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ADMIN_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
VIEWER_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
DEACTIVATED_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
DEVELOPER_AI_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")

_SEED_TS = datetime(2026, 9, 17, 7, 0, tzinfo=UTC)

SEED_USERS = (
    User(
        id=AUTHOR_ID,
        zitadel_sub=AUTHOR_SUB,
        email="author@veritask.test",
        name="Author One",
        role=Role.AUTHOR,
        is_active=True,
        created_at=_SEED_TS,
        updated_at=_SEED_TS,
    ),
    User(
        id=REVIEWER_ID,
        zitadel_sub=REVIEWER_SUB,
        email="reviewer@veritask.test",
        name="Reviewer One",
        role=Role.REVIEWER,
        is_active=True,
        created_at=_SEED_TS,
        updated_at=_SEED_TS,
    ),
    User(
        id=ADMIN_ID,
        zitadel_sub=ADMIN_SUB,
        email="admin@veritask.test",
        name="Admin One",
        role=Role.ADMIN,
        is_active=True,
        created_at=_SEED_TS,
        updated_at=_SEED_TS,
    ),
    User(
        id=VIEWER_ID,
        zitadel_sub=VIEWER_SUB,
        email="viewer@veritask.test",
        name="Viewer One",
        role=Role.VIEWER,
        is_active=True,
        created_at=_SEED_TS,
        updated_at=_SEED_TS,
    ),
    User(
        id=DEACTIVATED_ID,
        zitadel_sub=DEACTIVATED_SUB,
        email="deactivated@veritask.test",
        name="Deactivated One",
        role=Role.VIEWER,
        is_active=False,
        created_at=_SEED_TS,
        updated_at=_SEED_TS,
    ),
    User(
        id=DEVELOPER_AI_ID,
        zitadel_sub=DEVELOPER_AI_SUB,
        email="developer-ai@veritask.test",
        name="Developer AI One",
        role=Role.DEVELOPER_AI,
        is_active=True,
        created_at=_SEED_TS,
        updated_at=_SEED_TS,
    ),
)


def seed_users(db: DbSession) -> None:
    # TODO(SCRUM-89): production seed is Alembic, not this in-memory helper
    if db.query(User).count() > 0:
        return
    for user in SEED_USERS:
        db.merge(user)
    db.commit()


def apply_dev_zitadel_sub(db: DbSession, sub: str) -> None:
    # TODO(SCRUM-89): drop; leftover local.db must still pick up a new User ID
    if not sub:
        return
    user = db.get(User, AUTHOR_ID)
    if user is None:
        return
    user.zitadel_sub = sub
    db.commit()
