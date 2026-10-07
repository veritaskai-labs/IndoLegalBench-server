"""Adapter OpenAI-compatible. POST base_url apa adanya, tanpa menambah path."""

from app.modules.providers.adapters.base import (
    ConnectionTestResult,
    ProviderAdapter,
    ProviderResponse,
)
from app.modules.providers.adapters.http import (
    classify_error,
    http_failure,
    invalid_body,
    json_object,
    post_json,
)


class OpenAICompatibleAdapter(ProviderAdapter):
    name = "openai_compatible"

    def __init__(self, *, base_url: str, model_name: str, api_key: str) -> None:
        self.base_url = base_url
        self.model_name = model_name
        self._api_key = api_key

    def __repr__(self) -> str:
        return (
            f"OpenAICompatibleAdapter(base_url={self.base_url!r}, model_name={self.model_name!r})"
        )

    def test_connection(self) -> ConnectionTestResult:
        response, error, latency_ms = post_json(
            self.base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            body={
                "model": self.model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            },
        )
        if error is not None or response is None:
            return ConnectionTestResult(
                status="failed",
                message=error or "connection failed",
                error_category=classify_error(transport_error=error),
            )
        if not 200 <= response.status_code < 300:
            return ConnectionTestResult(
                status="failed",
                message=http_failure(response, self._api_key),
                error_category=classify_error(status_code=response.status_code),
            )
        payload = json_object(response)
        if payload is None or not isinstance(payload.get("choices"), list):
            return ConnectionTestResult(
                status="failed",
                message=invalid_body(response, self._api_key),
                error_category=classify_error(),
            )
        return ConnectionTestResult(status="ok", latency_ms=latency_ms)

    def ask(self, prompt: str) -> ProviderResponse:
        raise NotImplementedError
