"""Logika bisnis modul auth.

PBI-1 Login aman dan manajemen akses tim.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session as DbSession

from app.modules.auth import repository
from app.modules.auth.models import User
from app.modules.auth.oidc import OidcClient
from app.modules.auth.pending import pending_store
from app.modules.auth.pkce import code_challenge_s256, generate_code_verifier, generate_nonce
from app.modules.auth.schemas import UserCreateRequest, UserUpdateRoleRequest
from app.shared.config import get_settings
from app.shared.exceptions import (
    ConflictError,
    InvalidOidcStateError,
    NotFoundError,
    SessionExpiredError,
    UnauthenticatedError,
    UserDeactivatedError,
    UserNotRegisteredError,
    ValidationError,
)
from app.shared.security import CurrentUser


@dataclass(frozen=True)
class LoginStart:
    authorization_url: str


@dataclass(frozen=True)
class LoginSuccess:
    session_id: uuid.UUID
    redirect_url: str


def start_login(
    *, oidc: OidcClient, sub: str | None = None, email: str | None = None
) -> LoginStart:
    pending = pending_store.create(nonce=generate_nonce(), code_verifier=generate_code_verifier())
    extra: dict[str, str] = {}
    if sub:
        extra["sub"] = sub
    if email:
        extra["email"] = email
    url = oidc.authorization_url(
        state=pending.state,
        nonce=pending.nonce,
        code_challenge=code_challenge_s256(pending.code_verifier),
        extra_params=extra or None,
    )
    return LoginStart(authorization_url=url)


def complete_login(
    db: DbSession,
    *,
    oidc: OidcClient,
    code: str | None,
    state: str | None,
) -> LoginSuccess:
    if not code or not state:
        raise InvalidOidcStateError("Login state is missing. Start login again.")
    pending = pending_store.pop(state)
    if pending is None:
        raise InvalidOidcStateError("Login state is missing or expired. Start login again.")

    tokens = oidc.exchange_code(
        code=code,
        code_verifier=pending.code_verifier,
        expected_nonce=pending.nonce,
    )
    user = repository.get_user_by_sub(db, tokens.sub)
    if user is None and tokens.email:
        by_email = repository.get_user_by_email(db, tokens.email)
        if by_email is not None and by_email.zitadel_sub is None:
            user = by_email
    if user is None:
        raise UserNotRegisteredError("No platform account is mapped to this identity.")
    if not user.is_active:
        raise UserDeactivatedError("This account has been deactivated.")

    settings = get_settings()
    now = datetime.now(UTC)
    if user.zitadel_sub is None:
        user = repository.assign_zitadel_sub(db, user, tokens.sub, now=now)
    repository.update_user_profile(db, user, name=tokens.name, email=tokens.email, now=now)
    session = repository.create_session(
        db,
        user_id=user.id,
        expires_at=now + timedelta(minutes=settings.absolute_session_lifetime_minutes),
        zitadel_sid=tokens.sid,
        id_token=tokens.raw_id_token,
        now=now,
    )
    return LoginSuccess(session_id=session.id, redirect_url=settings.auth_done_url)


def logout(db: DbSession, *, oidc: OidcClient, session_id: uuid.UUID | None) -> str:
    id_token_hint: str | None = None
    if session_id is not None:
        session = repository.get_session(db, session_id)
        if session is not None:
            id_token_hint = session.id_token
        repository.delete_session(db, session_id)
    return oidc.end_session_url(id_token_hint=id_token_hint)


def _aware(stamp: datetime) -> datetime:
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp


def resolve_session(db: DbSession, session_id: uuid.UUID) -> User:
    """Load an active session and enforce idle plus the absolute cap.

    Other modules must go through get_current_user / require_role, not this
    function, except auth itself.
    """
    session = repository.get_session(db, session_id)
    if session is None:
        raise UnauthenticatedError("Authentication required.")

    settings = get_settings()
    now = datetime.now(UTC)
    last_activity = _aware(session.last_activity_at)
    expires_at = _aware(session.expires_at)
    idle = timedelta(minutes=settings.idle_timeout_minutes)
    if now > expires_at or now - last_activity > idle:
        repository.delete_session(db, session_id)
        raise SessionExpiredError("Sesi Anda telah berakhir, silakan masuk kembali.")

    user = repository.get_user_by_id(db, session.user_id)
    if user is None or not user.is_active:
        repository.delete_session(db, session_id)
        raise UnauthenticatedError("Authentication required.")

    repository.update_session_activity(db, session, last_activity_at=now)
    return user


def find_user_ids(db: DbSession, text: str) -> list[uuid.UUID]:
    """Dipakai modul audit untuk menyaring catatan berdasarkan nama pengguna (PBI-18 AC3)."""
    return repository.find_user_ids_matching(db, text)


def user_names(db: DbSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Display names for audit, version history, and snapshot authors.

    Ids with no user row are omitted from the result.
    """
    if not user_ids:
        return {}
    return repository.get_names(db, user_ids)


class AuthService:
    def __init__(self, db: DbSession):
        self.db = db

    def list_users(self, is_active: bool | None = None) -> list[User]:
        return repository.get_users(self.db, is_active=is_active)

    def create_member(self, payload: UserCreateRequest) -> User:
        existing_user = repository.get_user_by_email(self.db, payload.email)
        if existing_user:
            raise ConflictError(f"Email '{payload.email}' already exists")

        return repository.create_user(
            self.db,
            name=payload.name,
            email=payload.email,
            role=payload.role,
            zitadel_sub=None,
        )

    def update_member_role(
        self, user_id: uuid.UUID, payload: UserUpdateRoleRequest, current_user: CurrentUser
    ) -> User:
        # Together with CANNOT_DEACTIVATE_SELF this keeps at least one active
        # admin: the one making the request can neither demote nor
        # deactivate themself.
        if current_user.user_id == user_id:
            raise ValidationError(
                "Anda tidak dapat mengubah peran akun Anda sendiri.",
                code="CANNOT_CHANGE_OWN_ROLE",
            )
        user = repository.get_user_by_id(self.db, user_id)
        if not user:
            raise NotFoundError("User not found")
        return repository.update_user_role(self.db, user, payload.role)

    def deactivate_member(self, target_user_id: uuid.UUID, current_user: CurrentUser) -> User:
        if current_user.user_id == target_user_id:
            raise ValidationError(
                "Anda tidak dapat menonaktifkan akun Anda sendiri.",
                code="CANNOT_DEACTIVATE_SELF",
            )

        user = repository.get_user_by_id(self.db, target_user_id)
        if not user:
            raise NotFoundError("User not found")

        repository.delete_sessions_by_user_id(self.db, user.id)
        return repository.deactivate_user(self.db, user)
