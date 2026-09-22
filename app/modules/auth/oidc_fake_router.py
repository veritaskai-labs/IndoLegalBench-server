"""Fake OIDC HTTP routes (local/CI only).

- `GET /_fake/oidc/authorize` redirects to `/auth/callback`; `?sub=` picks seed `zitadel_sub`
- Strip with AUTH_OIDC_MODE=zitadel; not registered in staging/prod
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.modules.auth.oidc_fake import DEFAULT_SUB, get_fake_oidc_client
from app.shared.config import get_settings
from app.shared.exceptions import OidcExchangeFailedError

# include_in_schema=False disengaja. Router ini hanya terdaftar saat
# AUTH_OIDC_MODE=fake, jadi kalau ikut masuk skema, isi openapi.json
# berubah-ubah tergantung mode yang kebetulan aktif saat
# scripts/export_openapi.py dijalankan, dan gate kesegaran kontrak di CI
# jadi merah untuk orang yang .env-nya memakai zitadel.
#
# Alasan kedua: openapi.json diunggah CI sebagai artefak dan dipakai
# frontend untuk generate tipe. Pintu belakang login lokal tidak perlu
# ikut terdokumentasi di sana.
fake_router = APIRouter(tags=["fake-oidc"], include_in_schema=False)


@fake_router.get("/_fake/oidc/authorize")
def fake_authorize(request: Request) -> RedirectResponse:
    settings = get_settings()
    client = get_fake_oidc_client()
    query = request.query_params
    if query.get("client_id") != settings.zitadel_client_id:
        raise OidcExchangeFailedError("Unknown client_id.")
    redirect_uri = query.get("redirect_uri")
    state = query.get("state")
    nonce = query.get("nonce")
    challenge = query.get("code_challenge")
    if not redirect_uri or not state or not nonce or not challenge:
        raise OidcExchangeFailedError("Missing OIDC authorize parameters.")
    if redirect_uri != settings.redirect_uri:
        raise OidcExchangeFailedError("redirect_uri mismatch.")
    # TODO: auto-approves with no login UI; `sub` is attacker-chosen if fake is left on
    sub = query.get("sub") or query.get("login_hint") or DEFAULT_SUB
    code = client.issue_code(
        sub=sub,
        nonce=nonce,
        code_challenge=challenge,
        redirect_uri=redirect_uri,
        email=query.get("email"),
    )
    location = f"/auth/callback?{urlencode({'code': code, 'state': state})}"
    return RedirectResponse(url=location, status_code=302)


@fake_router.get("/_fake/oidc/end_session")
def fake_end_session(post_logout_redirect_uri: str | None = None) -> RedirectResponse:
    # TODO: ignores id_token_hint; Zitadel end_session will not
    settings = get_settings()
    target = post_logout_redirect_uri or settings.post_logout_redirect_uri
    return RedirectResponse(url=target, status_code=302)
