"""In-memory store for an unfinished OIDC login

- Holds `state`, `nonce`, `code_verifier` from GET /auth/login until /auth/callback
- `create()` → `pop(state)` once; missing or expired → None (TTL 10 min)
- Process-local: restart or extra replicas drop pending logins; user retries login
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel


class PendingAuth(BaseModel):
    state: str
    nonce: str
    code_verifier: str
    expires_at: datetime


class PendingAuthStore:
    # TODO: not shared across replicas; restart drops in-flight PKCE (Redis/DB later)
    def __init__(self, ttl_minutes: int = 10) -> None:
        self._ttl = timedelta(minutes=ttl_minutes)
        self._items: dict[str, PendingAuth] = {}

    def create(self, *, nonce: str, code_verifier: str) -> PendingAuth:
        now = datetime.now(UTC)
        pending = PendingAuth(
            state=uuid4().hex,
            nonce=nonce,
            code_verifier=code_verifier,
            expires_at=now + self._ttl,
        )
        self._items[pending.state] = pending
        return pending

    def pop(self, state: str) -> PendingAuth | None:
        pending = self._items.pop(state, None)
        if pending is None:
            return None
        if pending.expires_at <= datetime.now(UTC):
            return None
        return pending


pending_store = PendingAuthStore()
