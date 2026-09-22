"""Test goal: prove OUR Zitadel client wiring is correct, not that Zitadel Cloud works.

- No Cloud call: these asserts are URL shape + fail-fast config. Live IdP would make CI depend on network, tenant, and secrets — flaky for a unit test.
- Login URL must hit this issuer with Auth Code + PKCE (`S256`, `response_type=code`, openid scopes).
- Logout URL must include `id_token_hint` and `post_logout_redirect_uri` so Zitadel can end the IdP session.
- Missing `ZITADEL_ISSUER` must fail at client init (not on the first redirect).
- Token/JWKS/userinfo against a real issuer belong in a separate integration test.
"""

import pytest

from app.modules.auth.oidc_zitadel import ZitadelOidcClient
from app.shared.config import Settings


def test_zitadel_authorization_and_end_session_urls():
    # TODO: does not call discovery, token, JWKS, or userinfo
    settings = Settings(
        auth_oidc_mode="zitadel",
        zitadel_issuer="https://dev-environment-xxxx.zitadel.cloud",
        zitadel_client_id="243864426485212395@example",
        zitadel_redirect_uri="http://localhost:8000/auth/callback",
        cors_origins=["http://localhost:3000"],
    )
    client = ZitadelOidcClient(settings)
    url = client.authorization_url(
        state="state-1",
        nonce="nonce-1",
        code_challenge="challenge-1",
    )
    assert url.startswith("https://dev-environment-xxxx.zitadel.cloud/oauth/v2/authorize?")
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "scope=openid+profile+email" in url or "scope=openid%20profile%20email" in url

    with_sub = client.authorization_url(
        state="state-1",
        nonce="nonce-1",
        code_challenge="challenge-1",
        extra_params={"sub": "attacker", "login_hint": "keep-me"},
    )
    assert "sub=attacker" not in with_sub
    assert "login_hint=keep-me" in with_sub

    end = client.end_session_url(id_token_hint="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.dummy")
    assert end.startswith("https://dev-environment-xxxx.zitadel.cloud/oidc/v1/end_session?")
    assert "id_token_hint=" in end
    assert "post_logout_redirect_uri=" in end


def test_zitadel_mode_requires_issuer():
    settings = Settings(auth_oidc_mode="zitadel", zitadel_issuer="", zitadel_client_id="client")
    with pytest.raises(RuntimeError, match="ZITADEL_ISSUER"):
        ZitadelOidcClient(settings)
