"""Zitadel OIDC adapter (`AUTH_OIDC_MODE=zitadel`).

- Auth Code + PKCE: authorize, discovery, token, userinfo fallback, end_session
- JWT/JWKS verify lives in `oidc_jwt`; this file does not write users/sessions
- Requires `ZITADEL_ISSUER`, `ZITADEL_CLIENT_ID`
"""

import logging
from urllib.parse import urlencode

import httpx

from app.modules.auth.oidc import TokenResult
from app.modules.auth.oidc_jwt import display_name, verify_id_token
from app.shared.config import Settings
from app.shared.exceptions import DomainError, OidcExchangeFailedError

logger = logging.getLogger(__name__)


class ZitadelOidcClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.zitadel_issuer:
            raise RuntimeError("ZITADEL_ISSUER is required when AUTH_OIDC_MODE=zitadel")
        if not settings.zitadel_client_id:
            raise RuntimeError("ZITADEL_CLIENT_ID is required when AUTH_OIDC_MODE=zitadel")
        self.settings = settings
        self._issuer = settings.zitadel_issuer.rstrip("/")
        self._discovery: dict | None = None

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        extra_params: dict[str, str] | None = None,
    ) -> str:
        params = {
            "client_id": self.settings.zitadel_client_id,
            "redirect_uri": self.settings.redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if extra_params:
            params.update({k: v for k, v in extra_params.items() if k not in {"sub", "email"}})
        return f"{self._issuer}/oauth/v2/authorize?{urlencode(params)}"

    def _discovery_doc(self, http: httpx.Client) -> dict:
        if self._discovery is not None:
            return self._discovery
        response = http.get(f"{self._issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        self._discovery = response.json()
        return self._discovery

    def exchange_code(self, *, code: str, code_verifier: str, expected_nonce: str) -> TokenResult:
        # TODO: untested against a live tenant (discovery / token / JWKS / nonce)
        try:
            with httpx.Client(timeout=15.0) as http:
                discovery = self._discovery_doc(http)
                data = {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.redirect_uri,
                    "client_id": self.settings.zitadel_client_id,
                    "code_verifier": code_verifier,
                }
                if self.settings.zitadel_client_secret:
                    data["client_secret"] = self.settings.zitadel_client_secret
                token_response = http.post(discovery["token_endpoint"], data=data)
                if token_response.status_code != 200:
                    logger.error("Zitadel token endpoint returned %s", token_response.status_code)
                    raise OidcExchangeFailedError(
                        "Could not complete the identity-provider handshake."
                    )
                body = token_response.json()
                raw_id_token = body["id_token"]
                claims = verify_id_token(
                    raw_id_token,
                    discovery,
                    http,
                    issuer=self._issuer,
                    audience=self.settings.zitadel_audience or self.settings.zitadel_client_id,
                    access_token=body.get("access_token"),
                )
                if claims.get("nonce") != expected_nonce:
                    raise OidcExchangeFailedError("Nonce mismatch.")
                email = claims.get("email")
                name = display_name(claims)
                if email is None or name is None:
                    access_token = body.get("access_token")
                    if access_token:
                        userinfo = self._userinfo(discovery, access_token, http)
                        email = email or userinfo.get("email")
                        name = name or display_name(userinfo)
                return TokenResult(
                    sub=str(claims["sub"]),
                    nonce=str(claims["nonce"]),
                    sid=str(claims["sid"]) if claims.get("sid") else None,
                    email=email,
                    name=name,
                    raw_id_token=raw_id_token,
                )
        except DomainError:
            raise
        except Exception as exc:
            logger.error("OIDC token exchange failed")
            raise OidcExchangeFailedError(
                "Could not complete the identity-provider handshake."
            ) from exc

    def _userinfo(self, discovery: dict, access_token: str, http: httpx.Client) -> dict:
        response = http.get(
            discovery["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()

    def end_session_url(self, *, id_token_hint: str | None = None) -> str:
        params = {
            "client_id": self.settings.zitadel_client_id,
            "post_logout_redirect_uri": self.settings.post_logout_redirect_uri,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{self._issuer}/oidc/v1/end_session?{urlencode(params)}"
