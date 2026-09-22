"""Peran pengguna dan penjaga akses berbasis peran (RBAC).

Sengaja ditaruh di shared, bukan di modul auth, karena seluruh modul
perlu memakainya untuk membatasi endpoint.

Implementasi verifikasi token Zitadel dikerjakan di PBI-1, sub task
"[BE] Integrasi Zitadel OpenID Connect". Untuk sekarang bagian itu
masih TODO dan get_current_user akan menolak semua request.
"""

from enum import StrEnum

from fastapi import Depends

from app.shared.exceptions import ForbiddenError, UnauthorizedError


class Role(StrEnum):
    AUTHOR = "author"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    VIEWER = "viewer"


class CurrentUser:
    """Pengguna yang sedang login, hasil pembacaan klaim token."""

    def __init__(self, user_id: str, email: str, role: Role) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role


def get_current_user() -> CurrentUser:
    """Ambil pengguna dari token ID Zitadel.

    TODO(PBI-1): verifikasi token OIDC ke Zitadel, baca klaim peran,
    cek akun masih aktif, cek idle timeout, lalu kembalikan CurrentUser.
    """
    # TODO(SCRUM-91): read veritask_session cookie / sessions row; this always 401s
    raise UnauthorizedError("Verifikasi token belum diimplementasikan (PBI-1)")


def require_roles(*allowed: Role):
    """Pembatas akses per peran, dipakai sebagai dependency di router.

    Contoh pemakaian:

        @router.post("/", dependencies=[Depends(require_roles(Role.AUTHOR))])
        def create_suite(...):
            ...
    """

    def guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise ForbiddenError("Peran Anda tidak berwenang atas aksi ini")
        return user

    return guard
