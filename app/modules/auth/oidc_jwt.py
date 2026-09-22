"""JWT/JWKS helpers for the Zitadel adapter.

- Display name from id_token / userinfo claims
- Verify signature, issuer, audience, and at_hash
"""

import httpx
from jose import jwk, jwt

from app.shared.exceptions import OidcExchangeFailedError

# Library algorithms supported by Zitadel.
# TODO: Determine which algos are actually used by Veritask's Zitadel / determine which algos are supported by python-jose
_ALLOWED_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}


def display_name(claims: dict) -> str | None:
    # Delete?
    name = claims.get("name")
    if name:
        return str(name)
    preferred = claims.get("preferred_username")
    if preferred:
        return str(preferred)
    joined = " ".join(
        part for part in (claims.get("given_name"), claims.get("family_name")) if part
    ).strip()
    return joined or None


def verify_id_token(
    raw_id_token: str,
    discovery: dict,
    http: httpx.Client,
    *,
    issuer: str,
    audience: str,
    access_token: str | None = None,
) -> dict:
    header = jwt.get_unverified_header(raw_id_token)
    alg = header.get("alg") or "RS256"
    if alg not in _ALLOWED_ALGS:
        raise OidcExchangeFailedError(f"Unsupported id_token alg {alg}.")
    jwks_response = http.get(discovery["jwks_uri"])
    jwks_response.raise_for_status()
    jwks = jwks_response.json()
    try:
        key_dict = next(key for key in jwks["keys"] if key.get("kid") == header.get("kid"))
    except StopIteration as exc:
        raise OidcExchangeFailedError(f"No JWKS key for kid={header.get('kid')}.") from exc
    key = jwk.construct(key_dict, algorithm=alg)
    return jwt.decode(
        raw_id_token,
        key,
        algorithms=[alg],
        audience=audience,
        issuer=issuer,
        access_token=access_token,
    )
