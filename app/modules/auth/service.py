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
from app.modules.auth.oidc import OidcClient
from app.modules.auth.pending import pending_store
from app.modules.auth.pkce import code_challenge_s256, generate_code_verifier, generate_nonce
from app.modules.auth.schemas import MeResponse
from app.shared.config import get_settings
from app.shared.exceptions import (
    InvalidOidcStateError,
    UnauthenticatedError,
    UserDeactivatedError,
    UserNotRegisteredError,
)
from app.shared.security import Role


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


def get_me(db: DbSession, *, session_id: uuid.UUID) -> MeResponse:
    # TODO(SCRUM-91): other routers still use get_current_user, which always 401s
    session = repository.touch_session(db, session_id)
    if session is None:
        raise UnauthenticatedError("Authentication required.")
    user = repository.get_user_by_id(db, session.user_id)
    if user is None or not user.is_active:
        raise UnauthenticatedError("Authentication required.")
    return MeResponse(id=user.id, name=user.name, email=user.email, role=Role(user.role))
