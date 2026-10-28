"""Unit and integration tests for auth repository data access logic.

Validates:
- get_users (with and without is_active filter)
- get_user_by_id and get_user_by_email
- create_user (storing user with nullable zitadel_sub)
- update_user_role
- deactivate_user (non-destructive status toggle)
- delete_sessions_by_user_id (using UserSession)
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.auth import repository
from app.modules.auth.models import UserSession
from app.shared.database import Base
from app.shared.security import Role


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite database session for repository tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_repo_create_user_nullable_zitadel(db_session):
    """Admin-created users should have zitadel_sub set to None."""
    user = repository.create_user(
        db_session,
        name="John Doe",
        email="john@veritask.ai",
        role=Role.CONTRIBUTOR,
        zitadel_sub=None,
    )

    assert user.id is not None
    assert user.name == "John Doe"
    assert user.email == "john@veritask.ai"
    assert user.role == Role.CONTRIBUTOR
    assert user.zitadel_sub is None
    assert user.is_active is True


def test_repo_get_user_by_id_and_email(db_session):
    created = repository.create_user(
        db_session,
        name="Jane Doe",
        email="jane@veritask.ai",
        role=Role.CONTRIBUTOR,
    )

    by_id = repository.get_user_by_id(db_session, created.id)
    assert by_id is not None
    assert by_id.id == created.id
    assert by_id.email == "jane@veritask.ai"

    by_email = repository.get_user_by_email(db_session, "jane@veritask.ai")
    assert by_email is not None
    assert by_email.id == created.id

    assert repository.get_user_by_id(db_session, uuid.uuid4()) is None
    assert repository.get_user_by_email(db_session, "nonexistent@veritask.ai") is None


def test_repo_get_users_filtering(db_session):
    u1 = repository.create_user(
        db_session, name="User 1", email="u1@veritask.ai", role=Role.CONTRIBUTOR
    )
    u2 = repository.create_user(
        db_session, name="User 2", email="u2@veritask.ai", role=Role.CONTRIBUTOR
    )
    repository.deactivate_user(db_session, u2)

    # All users
    all_users = repository.get_users(db_session)
    assert len(all_users) == 2

    # Active only
    active_users = repository.get_users(db_session, is_active=True)
    assert len(active_users) == 1
    assert active_users[0].id == u1.id

    # Inactive only
    inactive_users = repository.get_users(db_session, is_active=False)
    assert len(inactive_users) == 1
    assert inactive_users[0].id == u2.id


def test_repo_update_user_role(db_session):
    user = repository.create_user(
        db_session,
        name="Role Switcher",
        email="roleswitch@veritask.ai",
        role=Role.VIEWER,
    )

    updated = repository.update_user_role(db_session, user, Role.ADMIN)
    assert updated.role == Role.ADMIN

    # Fetch from fresh query
    fetched = repository.get_user_by_id(db_session, user.id)
    assert fetched is not None
    assert fetched.role == Role.ADMIN


def test_repo_deactivate_user(db_session):
    user = repository.create_user(
        db_session,
        name="To Deactivate",
        email="deactivate@veritask.ai",
        role=Role.CONTRIBUTOR,
    )
    assert user.is_active is True

    deactivated = repository.deactivate_user(db_session, user)
    assert deactivated.is_active is False

    # Confirm user still exists in database (non-destructive)
    fetched = repository.get_user_by_id(db_session, user.id)
    assert fetched is not None
    assert fetched.is_active is False


def test_repo_delete_sessions_by_user_id(db_session):
    user = repository.create_user(
        db_session,
        name="Session Owner",
        email="sessions@veritask.ai",
        role=Role.CONTRIBUTOR,
    )

    expiry = datetime.now(UTC) + timedelta(hours=1)

    s1 = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        expires_at=expiry,
    )
    s2 = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        expires_at=expiry,
    )
    db_session.add_all([s1, s2])
    db_session.commit()

    assert db_session.query(UserSession).filter(UserSession.user_id == user.id).count() == 2

    # Delete sessions
    deleted_count = repository.delete_sessions_by_user_id(db_session, user.id)
    assert deleted_count == 2

    # Verify no sessions remain
    remaining = db_session.query(UserSession).filter(UserSession.user_id == user.id).count()
    assert remaining == 0
