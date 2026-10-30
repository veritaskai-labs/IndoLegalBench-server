"""SCRUM-76 SCRUM-208: ask() on the three adapters, isolated with MockTransport."""

import json

import httpx
import pytest

from app.modules.providers.adapters import http as provider_http
from app.modules.providers.adapters.anthropic_messages import AnthropicMessagesAdapter
from app.modules.providers.adapters.base import FailureKind
from app.modules.providers.adapters.dispatch import _ADAPTERS, run_connection_test
from app.modules.providers.adapters.gemini_interactions import GeminiInteractionsAdapter
from app.modules.providers.adapters.http import clip
from app.modules.providers.adapters.mock_provider import MockProviderAdapter
from app.modules.providers.adapters.openai_compatible import OpenAICompatibleAdapter
from app.modules.providers.adapters.params import (
    ANTHROPIC_DEFAULT_MAX_TOKENS,
    ASK_TIMEOUT_SECONDS,
    ModelParameters,
)

_KEY = "sk-test-secret-value"  # pragma: allowlist secret


def _transport(monkeypatch, handler, seen=None):
    real_client = httpx.Client

    def factory(*args, **kwargs):
        if seen is not None:
            seen["client_timeout"] = kwargs.get("timeout")
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(provider_http.httpx, "Client", factory)


def _openai():
    return OpenAICompatibleAdapter(
        base_url="https://api.example.test/v1/chat/completions",
        model_name="aiyu-1",
        api_key=_KEY,
    )


def _gemini():
    return GeminiInteractionsAdapter(
        base_url="https://generativelanguage.googleapis.com/v1beta/interactions",
        model_name="gemini-2.5-flash",
        api_key=_KEY,
    )


def _anthropic():
    return AnthropicMessagesAdapter(
        base_url="https://api.anthropic.com/v1/messages",
        model_name="claude-sonnet",
        api_key=_KEY,
    )


def test_ask_prompt_only_uses_240s_read_timeout_and_no_system_message(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "aiyu-1-2026",
                "choices": [{"message": {"content": "jawaban"}, "finish_reason": "stop"}],
            },
        )

    _transport(monkeypatch, handler, seen)
    result = _openai().ask("Apa itu pasal 1?")
    assert result.text == "jawaban"
    assert result.model_version == "aiyu-1-2026"
    assert result.failure_kind is None
    assert result.error is None
    assert seen["body"]["messages"] == [{"role": "user", "content": "Apa itu pasal 1?"}]
    assert "max_tokens" not in seen["body"]
    timeout = seen["client_timeout"]
    assert timeout.read == ASK_TIMEOUT_SECONDS


def test_openai_maps_parameters_and_keeps_the_key_out_of_raw(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "aiyu-1",
                "choices": [
                    {
                        "message": {"content": f"ok {_KEY}"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    _transport(monkeypatch, handler)
    params = ModelParameters(temperature=0.3, effort="high", thinking=True, web_search=True)
    result = _openai().ask("pertanyaan", parameters=params, timeout=30)
    assert seen["body"]["temperature"] == 0.3
    assert seen["body"]["reasoning_effort"] == "high"
    assert "thinking" not in seen["body"]
    assert _KEY not in json.dumps(result.raw)
    assert _KEY not in (result.error or "")
    assert _KEY not in result.text


def test_openai_timeout_is_no_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    _transport(monkeypatch, handler)
    result = _openai().ask("pertanyaan")
    assert result.failure_kind == FailureKind.NO_RESPONSE
    assert result.text == ""


def test_openai_connect_error_is_no_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect")

    _transport(monkeypatch, handler)
    result = _openai().ask("pertanyaan")
    assert result.failure_kind == FailureKind.NO_RESPONSE


def test_openai_http_error_is_provider_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("boom")

    _transport(monkeypatch, handler)
    result = _openai().ask("pertanyaan")
    assert result.failure_kind == FailureKind.PROVIDER_ERROR


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, FailureKind.KEY_REJECTED),
        (403, FailureKind.KEY_REJECTED),
        (404, FailureKind.MODEL_NOT_FOUND),
        (429, FailureKind.PROVIDER_ERROR),
        (500, FailureKind.PROVIDER_ERROR),
    ],
)
def test_openai_http_status_maps_failure_kind(monkeypatch, status, kind):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f"nope {_KEY}")

    _transport(monkeypatch, handler)
    result = _openai().ask("pertanyaan")
    assert result.failure_kind == kind
    assert _KEY not in (result.error or "")
    assert _KEY not in json.dumps(result.raw)


def test_openai_empty_truncated_blocked_refused(monkeypatch):
    bodies = [
        (
            {"choices": [{"finish_reason": "stop"}], "usage": {"n": 1}},
            FailureKind.EMPTY,
        ),
        (
            {
                "model": "m",
                "choices": [{"message": {"content": "sebagian"}, "finish_reason": "length"}],
            },
            FailureKind.TRUNCATED,
        ),
        (
            {
                "model": "m",
                "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
            },
            FailureKind.BLOCKED,
        ),
        (
            {
                "model": "m",
                "choices": [
                    {"message": {"content": "", "refusal": "tidak bisa"}, "finish_reason": "stop"}
                ],
            },
            FailureKind.REFUSED,
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies[handler.index][0]
        handler.index += 1
        return httpx.Response(200, json=body)

    handler.index = 0
    _transport(monkeypatch, handler)
    adapter = _openai()
    for _, kind in bodies:
        assert adapter.ask("q").failure_kind == kind


def test_openai_invalid_body_is_provider_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["bukan-objek"])

    _transport(monkeypatch, handler)
    result = _openai().ask("q")
    assert result.failure_kind == FailureKind.PROVIDER_ERROR


def test_openai_missing_choices_is_provider_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m"})

    _transport(monkeypatch, handler)
    assert _openai().ask("q").failure_kind == FailureKind.PROVIDER_ERROR


def test_non_positive_timeout_is_rejected():
    with pytest.raises(ValueError):
        _openai().ask("q", timeout=0)


def test_gemini_success_drops_temperature_and_reads_outputs(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-goog-api-key")
        return httpx.Response(
            200,
            json={"model": "gemini-2.5-flash", "outputs": [{"text": "jawab"}]},
        )

    _transport(monkeypatch, handler)
    result = _gemini().ask("tanya", parameters=ModelParameters(temperature=0.3))
    assert result.text == "jawab"
    assert result.failure_kind is None
    assert "temperature" not in seen["body"]
    assert seen["body"]["input"] == "tanya"
    assert "generation_config" not in seen["body"]
    assert seen["key"] == _KEY


def test_gemini_interactions_steps_and_status(monkeypatch):
    payloads = [
        (
            {
                "status": "completed",
                "steps": [
                    {"type": "model_output", "content": "bukan-list"},
                    {"type": "user_input", "content": [{"type": "text", "text": "tanya"}]},
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "jawab"}, {"type": "other"}],
                    },
                ],
            },
            None,
            "jawab",
        ),
        (
            {
                "status": "incomplete",
                "steps": [
                    {"type": "model_output", "content": [{"type": "text", "text": "potong"}]}
                ],
            },
            FailureKind.TRUNCATED,
            "potong",
        ),
        (
            {"status": "failed", "steps": []},
            FailureKind.PROVIDER_ERROR,
            "",
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = payloads[handler.index][0]
        handler.index += 1
        return httpx.Response(200, json=payload)

    handler.index = 0
    _transport(monkeypatch, handler)
    adapter = _gemini()
    for _, kind, text in payloads:
        result = adapter.ask("q")
        assert result.failure_kind == kind
        assert result.text == text


def test_gemini_block_truncation_refusal_and_empty(monkeypatch):
    payloads = [
        ({"outputs": [{"text": ""}], "block_reason": "SAFETY"}, FailureKind.BLOCKED),
        ({"outputs": [{"text": "potong"}], "finish_reason": "MAX_TOKENS"}, FailureKind.TRUNCATED),
        ({"outputs": [{"text": ""}], "finish_reason": "refusal"}, FailureKind.REFUSED),
        ({"outputs": [{"text": ""}]}, FailureKind.EMPTY),
        ({}, FailureKind.EMPTY),
        ({"output_text": "dari output_text"}, None),
        ({"output": "string-langsung"}, None),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = payloads[handler.index][0]
        handler.index += 1
        return httpx.Response(200, json=payload)

    handler.index = 0
    _transport(monkeypatch, handler)
    adapter = _gemini()
    for _, kind in payloads:
        assert adapter.ask("q").failure_kind == kind


def test_gemini_invalid_and_http_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if handler.calls == 0:
            handler.calls += 1
            return httpx.Response(200, text="bukan json")
        return httpx.Response(404, json={"error": f"missing {_KEY}"})

    handler.calls = 0
    _transport(monkeypatch, handler)
    adapter = _gemini()
    assert adapter.ask("q").failure_kind == FailureKind.PROVIDER_ERROR
    denied = adapter.ask("q")
    assert denied.failure_kind == FailureKind.MODEL_NOT_FOUND
    assert _KEY not in json.dumps(denied.raw)


def test_anthropic_sends_default_max_tokens_and_reads_text(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-4",
                "content": [{"type": "text", "text": "jawab"}],
                "stop_reason": "end_turn",
            },
        )

    _transport(monkeypatch, handler)
    result = _anthropic().ask("tanya")
    assert result.text == "jawab"
    assert result.model_version == "claude-sonnet-4"
    assert seen["body"]["max_tokens"] == ANTHROPIC_DEFAULT_MAX_TOKENS
    assert seen["body"]["messages"] == [{"role": "user", "content": "tanya"}]
    assert "system" not in seen["body"]


def test_anthropic_explicit_max_tokens_and_failure_kinds(monkeypatch):
    seen = {}
    payloads = [
        (
            {"content": [{"type": "text", "text": "x"}], "stop_reason": "max_tokens"},
            FailureKind.TRUNCATED,
        ),
        (
            {"content": [{"type": "text", "text": ""}], "stop_reason": "refusal"},
            FailureKind.REFUSED,
        ),
        ({"content": [{"type": "text", "text": ""}], "stop_reason": "end_turn"}, FailureKind.EMPTY),
        ({"content": "bukan-list"}, FailureKind.PROVIDER_ERROR),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen["max_tokens"] = json.loads(request.content)["max_tokens"]
        payload = payloads[handler.index][0]
        handler.index += 1
        return httpx.Response(200, json=payload)

    handler.index = 0
    _transport(monkeypatch, handler)
    adapter = _anthropic()
    for _, kind in payloads:
        assert adapter.ask("q", parameters=ModelParameters(max_tokens=32)).failure_kind == kind
    assert seen["max_tokens"] == 32


def test_anthropic_invalid_json_and_rejected_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if handler.calls == 0:
            handler.calls += 1
            return httpx.Response(200, text="bukan json")
        return httpx.Response(401, text=_KEY)

    handler.calls = 0
    _transport(monkeypatch, handler)
    adapter = _anthropic()
    assert adapter.ask("q", parameters=ModelParameters(max_tokens=32)).failure_kind == (
        FailureKind.PROVIDER_ERROR
    )
    denied = adapter.ask("q")
    assert denied.failure_kind == FailureKind.KEY_REJECTED
    assert _KEY not in (denied.error or "")


@pytest.mark.parametrize(
    ("behavior", "kind"),
    [
        ("success", None),
        ("slow", None),
        ("error", FailureKind.PROVIDER_ERROR),
        ("empty", FailureKind.EMPTY),
        ("truncated", FailureKind.TRUNCATED),
        ("blocked", FailureKind.BLOCKED),
        ("refusal", FailureKind.REFUSED),
        ("key_rejected", FailureKind.KEY_REJECTED),
        ("model_not_found", FailureKind.MODEL_NOT_FOUND),
    ],
)
def test_mock_provider_nine_behaviors(behavior, kind):
    result = MockProviderAdapter(behavior).ask("pertanyaan mentah")
    assert result.failure_kind == kind
    if behavior == "success":
        assert result.text
    if behavior == "slow":
        assert result.latency_ms >= 240_000
        assert result.text


def test_mock_unknown_behavior_is_rejected():
    with pytest.raises(ValueError):
        MockProviderAdapter("nope").ask("q")


def test_mock_is_not_a_dispatchable_product():
    assert "mock" not in _ADAPTERS
    assert MockProviderAdapter not in _ADAPTERS.values()


def test_mock_connection_does_not_use_the_network():
    result = MockProviderAdapter("success").test_connection()
    assert result.status == "ok"


def test_clip_drops_secret_and_bounds_length():
    assert clip("   ", "") == "connection test failed"
    assert clip(f"prefix {_KEY} suffix", _KEY) == "prefix suffix"
    long = "a" * 250
    assert len(clip(long, "")) == 200


def test_adapter_repr_hides_the_key():
    assert "aiyu-1" in repr(_openai())
    assert "gemini-2.5-flash" in repr(_gemini())
    assert "claude-sonnet" in repr(_anthropic())
    assert _KEY not in repr(_openai())


def test_unknown_provider_type_is_not_dispatched():
    result = run_connection_test(
        provider_type="mock",
        base_url="https://example.test",
        model_name="m",
        api_key=_KEY,
    )
    assert result.status == "failed"
