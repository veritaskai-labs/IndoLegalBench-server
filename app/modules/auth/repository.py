"""Akses database modul auth.

ATURAN: hanya auth/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session as DbSession

from app.modules.auth.models import User, UserSession


def get_user_by_sub(db: DbSession, zitadel_sub: str) -> User | None:
    return db.query(User).filter(User.zitadel_sub == zitadel_sub).first()


def get_user_by_email(db: DbSession, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def assign_zitadel_sub(
    db: DbSession, user: User, zitadel_sub: str, *, now: datetime | None = None
) -> User:
    user.zitadel_sub = zitadel_sub
    user.updated_at = now or datetime.now(UTC)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_id(db: DbSession, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def update_user_profile(
    db: DbSession,
    user: User,
    *,
    name: str | None,
    email: str | None,
    now: datetime | None = None,
) -> User:
    # TODO(SCRUM-89): directory of record is the users table, not IdP
    if not name and not email:
        return user
    if name:
        user.name = name
    if email:
        user.email = email
    user.updated_at = now or datetime.now(UTC)
    db.commit()
    db.refresh(user)
    return user


def create_session(
    db: DbSession,
    *,
    user_id: uuid.UUID,
    expires_at: datetime,
    zitadel_sid: str | None = None,
    id_token: str | None = None,
    now: datetime | None = None,
) -> UserSession:
    stamp = now or datetime.now(UTC)
    session = UserSession(
        user_id=user_id,
        created_at=stamp,
        last_activity_at=stamp,
        expires_at=expires_at,
        zitadel_sid=zitadel_sid,
        id_token=id_token,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: DbSession, session_id: uuid.UUID) -> UserSession | None:
    session = db.get(UserSession, session_id)
    if session is None:
        return None
    now = datetime.now(UTC)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    # TODO(SCRUM-91): compare last_activity_at + idle window, not only expires_at
    if expires_at <= now:
        db.delete(session)
        db.commit()
        return None
    return session


def delete_session(db: DbSession, session_id: uuid.UUID) -> None:
    session = db.get(UserSession, session_id)
    if session is None:
        return
    db.delete(session)
    db.commit()


def touch_session(
    db: DbSession, session_id: uuid.UUID, *, now: datetime | None = None
) -> UserSession | None:
    session = get_session(db, session_id)
    if session is None:
        return None
    session.last_activity_at = now or datetime.now(UTC)
    # TODO(SCRUM-91): slide expires_at on activity so idle timeout actually resets
    db.commit()
    db.refresh(session)
    return session
