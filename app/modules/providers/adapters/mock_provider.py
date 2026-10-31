"""Adapter palsu untuk tes worker. Tidak didaftarkan di dispatch."""

from app.modules.providers.adapters.base import (
    ConnectionTestResult,
    FailureKind,
    ProviderAdapter,
    ProviderResponse,
)
from app.modules.providers.adapters.params import ASK_TIMEOUT_SECONDS, ModelParameters

_BEHAVIORS = frozenset(
    {
        "success",
        "slow",
        "error",
        "empty",
        "truncated",
        "blocked",
        "refusal",
        "key_rejected",
        "model_not_found",
    }
)

_KINDS = {
    "error": FailureKind.PROVIDER_ERROR,
    "empty": FailureKind.EMPTY,
    "truncated": FailureKind.TRUNCATED,
    "blocked": FailureKind.BLOCKED,
    "refusal": FailureKind.REFUSED,
    "key_rejected": FailureKind.KEY_REJECTED,
    "model_not_found": FailureKind.MODEL_NOT_FOUND,
}


class MockProviderAdapter(ProviderAdapter):
    name = "mock"

    def __init__(self, behavior: str) -> None:
        if behavior not in _BEHAVIORS:
            raise ValueError(f"unknown mock behavior: {behavior}")
        self.behavior = behavior

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(status="ok", latency_ms=1)

    def ask(
        self,
        prompt: str,
        *,
        parameters: ModelParameters | None = None,
        timeout: float = ASK_TIMEOUT_SECONDS,
    ) -> ProviderResponse:
        del parameters, timeout
        if self.behavior in ("success", "slow"):
            return ProviderResponse(
                text=f"mock:{prompt}",
                model_version="mock-1",
                latency_ms=240_000 if self.behavior == "slow" else 5,
                raw={"behavior": self.behavior},
            )
        kind = _KINDS[self.behavior]
        return ProviderResponse(
            text="sebagian" if kind == FailureKind.TRUNCATED else "",
            model_version="mock-1",
            latency_ms=5,
            raw={"behavior": self.behavior},
            error=kind,
            failure_kind=kind,
        )
