"""OIDC port used by auth service (IdP-agnostic).

- Implementors: `ZitadelOidcClient`, `FakeOidcClient` (`authorization_url`, `exchange_code`, `end_session_url`)
- Factory: `get_oidc_client()` from `AUTH_OIDC_MODE`
- `TokenResult`: `sub`, `nonce`, optional `sid`, raw `id_token` (logout)
"""

from typing import Protocol

from pydantic import BaseModel

from app.shared.config import Settings

_FAKE_OIDC_FORBIDDEN_ENVS = frozenset({"staging", "production"})


def ensure_fake_oidc_allowed(settings: Settings) -> None:
    """Fake IdP is local/CI only. Staging and production must use Zitadel."""
    if settings.auth_oidc_mode != "fake":
        return
    env = settings.app_env.strip().lower()
    if env in _FAKE_OIDC_FORBIDDEN_ENVS:
        raise RuntimeError(
            "AUTH_OIDC_MODE=fake is not allowed when APP_ENV is staging or production"
        )


class TokenResult(BaseModel):
    sub: str
    nonce: str
    sid: str | None = None
    email: str | None = None
    name: str | None = None
    raw_id_token: str


class OidcClient(Protocol):
    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        extra_params: dict[str, str] | None = None,
    ) -> str: ...

    def exchange_code(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> TokenResult: ...

    def end_session_url(self, *, id_token_hint: str | None = None) -> str: ...


def oidc_client_for(settings: Settings) -> OidcClient:
    from app.modules.auth.oidc_fake import get_fake_oidc_client
    from app.modules.auth.oidc_zitadel import ZitadelOidcClient

    if settings.auth_oidc_mode == "zitadel":
        return ZitadelOidcClient(settings)
    ensure_fake_oidc_allowed(settings)
    return get_fake_oidc_client()


def get_oidc_client() -> OidcClient:
    from app.shared.config import get_settings

    return oidc_client_for(get_settings())
