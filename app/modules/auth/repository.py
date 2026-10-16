"""Akses database modul auth.

ATURAN: hanya auth/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DbSession

from app.modules.auth.models import User, UserSession
from app.shared.security import Role


def get_user_by_sub(db: DbSession, zitadel_sub: str) -> User | None:
    return db.query(User).filter(User.zitadel_sub == zitadel_sub).first()


def get_user_by_email(db: DbSession, email: str) -> User | None:
    # Case-insensitive: Zitadel may send `Staff10@...` for a row stored as
    # `staff10@...`, and an exact match would answer USER_NOT_REGISTERED.
    return db.query(User).filter(func.lower(User.email) == email.lower()).first()


def get_user_by_id(db: DbSession, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def get_users(db: DbSession, is_active: bool | None = None) -> list[User]:
    query = db.query(User)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    return query.order_by(User.name, User.email).all()


def find_user_ids_matching(db: DbSession, text: str) -> list[uuid.UUID]:
    """Id pengguna yang nama atau emailnya memuat text, tanpa peduli kapital.

    Termasuk pengguna nonaktif. '%' dan '_' dari pemanggil dicari apa adanya.
    """
    pola = "%" + _escape_like(text.lower()) + "%"
    baris = (
        db.query(User.id)
        .filter(
            or_(
                func.lower(User.name).like(pola, escape="\\"),
                func.lower(User.email).like(pola, escape="\\"),
            )
        )
        .order_by(User.name)
        .all()
    )
    return [user_id for (user_id,) in baris]


def get_active_reviewer_ids(db: DbSession) -> list[uuid.UUID]:
    baris = (
        db.query(User.id)
        .filter(User.role == Role.REVIEWER, User.is_active.is_(True))
        .order_by(User.id)
        .all()
    )
    return [user_id for (user_id,) in baris]


def get_names(db: DbSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    baris = db.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
    return dict(baris)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def create_user(
    db: DbSession,
    *,
    name: str,
    email: str,
    role: Role,
    zitadel_sub: str | None = None,
) -> User:
    user = User(
        name=name,
        email=email,
        role=role,
        zitadel_sub=zitadel_sub,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user_role(db: DbSession, user: User, role: Role) -> User:
    user.role = role
    db.commit()
    db.refresh(user)
    return user


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


def assign_zitadel_sub(
    db: DbSession, user: User, zitadel_sub: str, *, now: datetime | None = None
) -> User:
    user.zitadel_sub = zitadel_sub
    user.updated_at = now or datetime.now(UTC)
    db.commit()
    db.refresh(user)
    return user


def deactivate_user(db: DbSession, user: User) -> User:
    user.is_active = False
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
    return db.get(UserSession, session_id)


def delete_session(db: DbSession, session_id: uuid.UUID) -> None:
    session = db.get(UserSession, session_id)
    if session is None:
        return
    db.delete(session)
    db.commit()


def delete_sessions_by_user_id(db: DbSession, user_id: uuid.UUID) -> int:
    deleted_count = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted_count


def update_session_activity(
    db: DbSession,
    session: UserSession,
    *,
    last_activity_at: datetime,
) -> UserSession:
    session.last_activity_at = last_activity_at
    db.commit()
    db.refresh(session)
    return session
