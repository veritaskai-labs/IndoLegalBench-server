"""PKCE + nonce helpers for Authorization Code Flow.

- `code_challenge` = SHA-256(`code_verifier`); send verifier on token exchange (RFC 7636)
- `nonce` binds the id_token to this login attempt
"""

import base64
import hashlib
import secrets


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def generate_nonce() -> str:
    return secrets.token_urlsafe(32)


def code_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
