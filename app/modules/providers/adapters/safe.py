"""Pembungkus adapter provider yang tidak membocorkan kredensial atau alamat endpoint.

Dipakai worker pengukuran (service.build_measurement_adapter) dan penilai LLM
(judge_service.build_judge, SCRUM-258): satu aturan scrub untuk kedua jalur.
"""

from app.modules.providers.adapters.ask_call import scrub
from app.modules.providers.adapters.base import FailureKind, ProviderResponse


class SafeProviderAdapter:
    """Jangan simpan credential/alamat endpoint dari body atau exception provider."""

    def __init__(self, adapter, secret, endpoint):
        self.adapter = adapter
        self._secret = secret
        self.endpoint = endpoint

    def ask(self, prompt, **kwargs):
        try:
            response = self.adapter.ask(prompt, **kwargs)
        except Exception:
            return ProviderResponse(
                text="",
                model_version="",
                latency_ms=0,
                raw={},
                error="provider call failed",
                failure_kind=FailureKind.PROVIDER_ERROR,
            )
        # Adapters may fall back to a configured alias; only the response is version evidence.
        observed = response.raw.get("model")
        response.model_version = observed if isinstance(observed, str) and observed.strip() else ""
        for value in (self._secret, self.endpoint):
            response.text = scrub(response.text, value)
            response.raw = scrub(response.raw, value)
            response.error = scrub(response.error, value)
            response.model_version = scrub(response.model_version, value)
        return response
