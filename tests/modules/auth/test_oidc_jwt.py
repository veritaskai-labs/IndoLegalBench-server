"""JWT/claim helpers used by the Zitadel adapter (no Cloud)."""

import pytest
from jose import jwt

from app.modules.auth.oidc_jwt import display_name, verify_id_token
from app.shared.exceptions import OidcExchangeFailedError


def test_display_name_prefers_name_then_username_then_given_family():
    assert display_name({"name": "Ada", "preferred_username": "ada"}) == "Ada"
    assert display_name({"preferred_username": "ada"}) == "ada"
    assert display_name({"given_name": "Ada", "family_name": "Lovelace"}) == "Ada Lovelace"
    assert display_name({}) is None


def test_verify_id_token_rejects_unsupported_alg():
    raw = jwt.encode({"sub": "x"}, "secret", algorithm="HS256")
    with pytest.raises(OidcExchangeFailedError, match="Unsupported id_token alg HS256"):
        verify_id_token(
            raw,
            {"jwks_uri": "http://example.invalid/jwks"},
            http=None,  # type: ignore[arg-type]
            issuer="http://issuer",
            audience="client",
        )
