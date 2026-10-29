"""Endpoint HTTP modul auth.

PBI-1 Login aman dan manajemen akses tim.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.
"""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.modules.auth import service
from app.modules.auth.cookies import (
    clear_session_cookie,
    session_id_from_cookie,
    set_session_cookie,
)
from app.modules.auth.oidc import OidcClient, get_oidc_client
from app.modules.auth.schemas import (
    ConfirmationRequiredBody,
    ErrorBody,
    MeResponse,
    UserCreateRequest,
    UserResponse,
    UserUpdateRoleRequest,
)
from app.modules.auth.service import AuthService
from app.shared.config import get_settings
from app.shared.database import get_db
from app.shared.exceptions import DomainError
from app.shared.security import CurrentUser, Role, get_current_user, require_roles

router = APIRouter()

auth_router = APIRouter(tags=["auth"])


def _me_body(user: CurrentUser) -> MeResponse:
    return MeResponse(id=user.user_id, name=user.name, email=user.email, role=user.role)


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


@auth_router.get("/auth/login", status_code=302, summary="Mulai login OIDC")
def login(
    sub: str | None = None,
    email: str | None = None,
    oidc: OidcClient = Depends(get_oidc_client),
) -> RedirectResponse:
    # TODO: drop `sub` and `email`; fake-only backdoor to pick a seed identity
    result = service.start_login(oidc=oidc, sub=sub, email=email)
    return RedirectResponse(url=result.authorization_url, status_code=302)


@auth_router.get(
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


@auth_router.post("/auth/logout", status_code=302, summary="Hapus sesi dan logout IdP")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    oidc: OidcClient = Depends(get_oidc_client),
) -> RedirectResponse:
    settings = get_settings()
    url = service.logout(db, oidc=oidc, session_id=session_id_from_cookie(request, settings))
    response = RedirectResponse(url=url, status_code=302)
    clear_session_cookie(response, settings)
    return response


@auth_router.get(
    "/auth/done",
    response_model=MeResponse,
    summary="Landing lokal setelah login",
    responses=_SESSION_RESPONSES,
)
def auth_done(user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    """Same payload as /me. Used when there is no frontend on :3000."""
    return _me_body(user)


@auth_router.get(
    "/me",
    response_model=MeResponse,
    summary="Profil pengguna yang sedang login",
    responses=_SESSION_RESPONSES,
)
def me(user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return _me_body(user)


admin_router = APIRouter(
    prefix="/admin/users", tags=["Admin Members"], dependencies=[Depends(require_roles(Role.ADMIN))]
)


def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    return AuthService(db)


# PBI-24 AC8: ubah peran / nonaktifkan reviewer yang masih punya kasus menunggu
# penilaian dibalas 409 sampai Admin mengirim ulang dengan confirm=true.
_CONFIRM = Query(
    default=False,
    description="true setelah Admin menyetujui dialog konfirmasi dari respons 409",
)
_CONFIRM_RESPONSES: dict[int | str, dict] = {
    409: {
        "model": ConfirmationRequiredBody,
        "description": (
            "Masih ada kasus menunggu penilaian anggota ini (`CONFIRMATION_REQUIRED`). "
            "Kirim ulang dengan confirm=true setelah Admin setuju."
        ),
    }
}


@admin_router.get("", response_model=list[UserResponse])
def get_users(
    is_active: bool | None = Query(default=None), service: AuthService = Depends(get_auth_service)
):
    return service.list_users(is_active=is_active)


@admin_router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreateRequest, service: AuthService = Depends(get_auth_service)):
    return service.create_member(payload)


@admin_router.patch("/{user_id}", response_model=UserResponse, responses=_CONFIRM_RESPONSES)
def update_user_role(
    user_id: UUID,
    payload: UserUpdateRoleRequest,
    confirm: bool = _CONFIRM,
    current_user: CurrentUser = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    return service.update_member_role(user_id, payload, current_user=current_user, confirm=confirm)


@admin_router.post(
    "/{user_id}/deactivate", response_model=UserResponse, responses=_CONFIRM_RESPONSES
)
def deactivate_user(
    user_id: UUID,
    confirm: bool = _CONFIRM,
    current_user: CurrentUser = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    return service.deactivate_member(
        target_user_id=user_id, current_user=current_user, confirm=confirm
    )


@admin_router.post("/{user_id}/activate", response_model=UserResponse)
def activate_user(user_id: UUID, service: AuthService = Depends(get_auth_service)):
    return service.activate_member(target_user_id=user_id)


router.include_router(auth_router)
router.include_router(admin_router)
