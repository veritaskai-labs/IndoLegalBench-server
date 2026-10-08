"""Peran pengguna dan penjaga akses berbasis peran (RBAC).

Sengaja ditaruh di shared, bukan di modul auth, karena seluruh modul
perlu memakainya untuk membatasi endpoint.

Sesi platform: cookie `veritask_session` → baris `sessions` → `users.role`.
Idle timeout dan sliding `last_activity_at` ada di auth.service.resolve_session.
"""

import uuid
from enum import StrEnum

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.shared import request_context
from app.shared.config import get_settings
from app.shared.database import get_db
from app.shared.exceptions import ForbiddenError, UnauthenticatedError


class Role(StrEnum):
    AUTHOR = "author"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    VIEWER = "viewer"


class CurrentUser:
    """Pengguna yang sedang login, dari sesi platform (bukan klaim IdP)."""

    def __init__(self, user_id: uuid.UUID, name: str, email: str, role: Role) -> None:
        self.user_id = user_id
        self.name = name
        self.email = email
        self.role = role


def get_current_user(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Ambil pengguna dari cookie sesi. Tanpa sesi → 401 UNAUTHENTICATED.

    Idle melebihi batas → hapus sesi, 401 SESSION_EXPIRED, cookie dihapus
    di domain_error_handler. Request yang lolos memperbarui last_activity_at
    dan me-refresh max-age cookie.
    """
    # Imported here, not at the top: auth.models imports Role from this module,
    # so a top-level import of auth.service is circular.
    from app.modules.auth import service as auth_service
    from app.modules.auth.cookies import session_id_from_cookie, set_session_cookie

    settings = get_settings()
    session_id = session_id_from_cookie(request, settings)
    if session_id is None:
        raise UnauthenticatedError("Authentication required.")
    user = auth_service.resolve_session(db, session_id)
    set_session_cookie(response, settings, session_id)
    current = CurrentUser(
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=Role(user.role),
    )
    _ikat_aktor(request, db, current)
    return current


def require_roles(*allowed: Role):
    """Pembatas akses per peran, dipakai sebagai dependency di router.

    Tanpa sesi → 401 UNAUTHENTICATED. Peran salah → 403 FORBIDDEN.

    Contoh pemakaian:

        @router.post("/", dependencies=[Depends(require_role(Role.ADMIN))])
        def admin_only(...):
            ...
    """

    def guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise ForbiddenError("Peran Anda tidak berwenang atas aksi ini")
        return user

    return guard


def _ikat_aktor(request: Request, db: Session, user: CurrentUser) -> None:
    """PBI-18: pencatat audit membaca pelaku dari session database ini."""
    request_context.bind(
        db,
        user_id=user.user_id,
        role=user.role,
        request_id=getattr(request.state, "request_id", None),
    )


require_role = require_roles
