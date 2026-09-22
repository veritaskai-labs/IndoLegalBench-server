"""Endpoint HTTP modul auth.

PBI-1 Login aman dan manajemen akses tim.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.
"""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.modules.auth import service
from app.modules.auth.cookies import clear_session_cookie, set_session_cookie
from app.modules.auth.oidc import OidcClient, get_oidc_client
from app.modules.auth.schemas import ErrorBody, MeResponse
from app.shared.config import get_settings
from app.shared.database import get_db
from app.shared.exceptions import DomainError, UnauthenticatedError

router = APIRouter(tags=["auth"])


def _session_id_from_cookie(request: Request) -> UUID | None:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


# Dideklarasikan supaya ikut terbit di openapi.json. Tanpa ini kontrak
# hanya memuat jalur sukses, dan frontend harus menebak bentuk error lalu
# menulis tipenya sendiri.
#
# Dua kode berbeda berbagi status 401 dan memang disengaja: frontend
# membedakan "belum login" dari "sesi habis" lewat `code`, bukan lewat
# status, supaya bisa menampilkan pesan "Sesi Anda telah berakhir".
_SESSION_RESPONSES: dict[int | str, dict] = {
    401: {
        "model": ErrorBody,
        "description": (
            "Tidak ada sesi aktif (`UNAUTHENTICATED`), atau sesi sudah melewati "
            "batas idle (`SESSION_EXPIRED`)."
        ),
    }
}


def _done_url_with_error(done_url: str, code: str) -> str:
    """Tempelkan kode error sebagai query param di URL /auth/done."""
    separator = "&" if "?" in done_url else "?"
    return f"{done_url}{separator}{urlencode({'error': code})}"


@router.get("/auth/login", status_code=302, summary="Mulai login OIDC")
def login(
    sub: str | None = None,
    email: str | None = None,
    oidc: OidcClient = Depends(get_oidc_client),
) -> RedirectResponse:
    # TODO: drop `sub` and `email`; fake-only backdoor to pick a seed identity
    result = service.start_login(oidc=oidc, sub=sub, email=email)
    return RedirectResponse(url=result.authorization_url, status_code=302)


@router.get(
    "/auth/callback",
    status_code=302,
    summary="Callback OIDC",
    description=(
        "Selalu membalas 302, termasuk saat gagal. Kegagalan dibelokkan ke "
        "`/auth/done?error=<CODE>` dengan kode seperti `USER_NOT_REGISTERED`, "
        "`USER_DEACTIVATED`, `INVALID_OIDC_STATE`, atau `OIDC_EXCHANGE_FAILED`. "
        "Endpoint ini adalah navigasi halaman penuh dari IdP, jadi sengaja tidak "
        "pernah membalas badan error JSON."
    ),
)
def callback(
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
    oidc: OidcClient = Depends(get_oidc_client),
) -> RedirectResponse:
    settings = get_settings()

    # Endpoint ini adalah navigasi halaman penuh yang datang dari IdP,
    # bukan XHR. Kalau DomainError dibiarkan naik, handler global di
    # main.py membalas JSON dan browser menampilkan JSON mentah itu ke
    # pengguna. Frontend (SCRUM-94) sudah menunggu kodenya sebagai query
    # param di /auth/done supaya bisa menampilkan halaman error yang
    # sesuai, jadi seluruh kegagalan di sini dibelokkan ke sana.
    #
    # Hanya callback yang diperlakukan begini. /me dan endpoint lain
    # tetap membalas JSON, karena frontend memanggilnya lewat fetch dan
    # membaca field code dari body.
    try:
        result = service.complete_login(db, oidc=oidc, code=code, state=state)
    except DomainError as error:
        return RedirectResponse(
            url=_done_url_with_error(settings.auth_done_url, error.code),
            status_code=302,
        )

    response = RedirectResponse(url=result.redirect_url, status_code=302)
    set_session_cookie(response, settings, result.session_id)
    return response


@router.post("/auth/logout", status_code=302, summary="Hapus sesi dan logout IdP")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    oidc: OidcClient = Depends(get_oidc_client),
) -> RedirectResponse:
    settings = get_settings()
    url = service.logout(db, oidc=oidc, session_id=_session_id_from_cookie(request))
    response = RedirectResponse(url=url, status_code=302)
    clear_session_cookie(response, settings)
    return response


@router.get(
    "/auth/done",
    response_model=MeResponse,
    summary="Landing lokal setelah login",
    responses=_SESSION_RESPONSES,
)
def auth_done(request: Request, db: Session = Depends(get_db)) -> MeResponse:
    """Same payload as /me. Used when there is no frontend on :3000."""
    return me(request, db)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Profil pengguna yang sedang login",
    responses=_SESSION_RESPONSES,
)
def me(request: Request, db: Session = Depends(get_db)) -> MeResponse:
    session_id = _session_id_from_cookie(request)
    if session_id is None:
        raise UnauthenticatedError("Authentication required.")
    return service.get_me(db, session_id=session_id)
