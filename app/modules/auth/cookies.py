"""Platform session cookie (not Zitadel's cookie).

- Value = `sessions.id` (UUID); HttpOnly + SameSite=Lax
- Call from router only; service must not touch HTTP
"""

from uuid import UUID

from fastapi import Response

from app.shared.config import Settings


def set_session_cookie(response: Response, settings: Settings, session_id: UUID) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=str(session_id),
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.cookie_secure,  # TODO: false on local HTTP; must be true on HTTPS
        max_age=settings.idle_timeout_minutes * 60,  # TODO(SCRUM-91): browser max-age ≠ server idle
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
