"""Exception domain yang dipakai bersama seluruh modul.

Modul melempar exception dari sini, lalu handler di main.py yang
menerjemahkannya jadi HTTP response. Dengan begitu service.py tidak
perlu tahu apa-apa soal HTTP.
"""


class DomainError(Exception):
    """Induk seluruh error domain."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """Dipakai misalnya saat nama suite duplikat atau ID kasus bentrok."""

    status_code = 409
    code = "conflict"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class ForbiddenError(DomainError):
    """Dipakai saat peran pengguna tidak berwenang atas sebuah aksi."""

    status_code = 403
    code = "forbidden"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"  # TODO: fold into UnauthenticatedError (one 401 code)


class UnauthenticatedError(DomainError):
    status_code = 401
    code = "UNAUTHENTICATED"


class UserNotRegisteredError(DomainError):
    status_code = 403
    code = "USER_NOT_REGISTERED"


class UserDeactivatedError(DomainError):
    status_code = 403
    code = "USER_DEACTIVATED"


class InvalidOidcStateError(DomainError):
    status_code = 400
    code = "INVALID_OIDC_STATE"


class OidcExchangeFailedError(DomainError):
    """Pertukaran token OIDC gagal.

    Pesan error ini tidak sampai ke pengguna. /auth/callback membelokkan
    kegagalan ke /auth/done dan hanya membawa `code` di query param,
    sedangkan detailnya ditulis ke log server. Karena itu pesan yang agak
    rinci di adapter masih aman.

    Yang perlu dijaga: jangan sampai ada endpoint lain yang membalas error
    ini sebagai JSON ke navigasi browser. Adapter Zitadel sendiri sudah
    memakai pesan umum dan menaruh detailnya di logger.
    """

    status_code = 400
    code = "OIDC_EXCHANGE_FAILED"
