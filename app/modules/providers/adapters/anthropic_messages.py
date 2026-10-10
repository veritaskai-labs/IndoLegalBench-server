"""Adapter Anthropic Messages API. POST base_url apa adanya."""

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


class AnthropicMessagesAdapter(ProviderAdapter):
    name = "anthropic_messages"

    def __init__(self, *, base_url: str, model_name: str, api_key: str) -> None:
        self.base_url = base_url
        self.model_name = model_name
        self._api_key = api_key

    def __repr__(self) -> str:
        return (
            f"AnthropicMessagesAdapter(base_url={self.base_url!r}, model_name={self.model_name!r})"
        )

    def test_connection(self) -> ConnectionTestResult:
        response, error, latency_ms = post_json(
            self.base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            body={
                "model": self.model_name,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
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
        if payload is None or not isinstance(payload.get("content"), list):
            return ConnectionTestResult(
                status="failed",
                message=invalid_body(response, self._api_key),
                error_category=classify_error(),
            )
        return ConnectionTestResult(status="ok", latency_ms=latency_ms)

    def ask(self, prompt: str) -> ProviderResponse:
        raise NotImplementedError
