"""Bantuan ask(): timeout, scrub kredensial, dan jenis kegagalan.

Adapter tetap yang menyusun body. Modul ini hanya membaca respons.
"""

import httpx

from app.modules.providers.adapters.base import FailureKind, ProviderResponse
from app.modules.providers.adapters.http import clip, http_failure, json_object, post_json

_CONNECT_SECONDS = 15.0
_TRUNCATED = "truncated by length limit"
_EMPTY = "empty response"


def ask_client_timeout(seconds: float) -> httpx.Timeout:
    if seconds <= 0:
        raise ValueError("timeout must be positive")
    return httpx.Timeout(
        connect=_CONNECT_SECONDS,
        read=seconds,
        write=seconds,
        pool=_CONNECT_SECONDS,
    )


def perform_ask(
    *,
    url: str,
    headers: dict[str, str],
    body: dict,
    secret: str,
    timeout: float,
    model_name: str,
    interpret,
) -> ProviderResponse:
    """POST sekali, tanpa retry, lalu baca body sukses atau kegagalan."""
    response, error, latency_ms = post_json(
        url,
        headers=headers,
        body=body,
        timeout=ask_client_timeout(timeout),
    )
    failed = transport_failure(response, error, latency_ms, secret)
    if failed is not None:
        return failed
    return interpret(json_object(response), latency_ms, model_name, secret)


def scrub(value, secret: str):
    if isinstance(value, str):
        return value.replace(secret, "") if secret else value
    if isinstance(value, dict):
        return {key: scrub(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secret) for item in value]
    return value


def _raw_from_response(response: httpx.Response, secret: str) -> dict:
    payload = json_object(response)
    if payload is not None:
        return scrub(payload, secret)
    return {"status_code": response.status_code, "body": scrub(response.text or "", secret)}


def _http_failure_kind(status_code: int) -> FailureKind:
    if status_code in (401, 403):
        return FailureKind.KEY_REJECTED
    if status_code == 404:
        return FailureKind.MODEL_NOT_FOUND
    return FailureKind.PROVIDER_ERROR


def _no_response(message: str) -> bool:
    lower = message.lower()
    return "timed out" in lower or "could not connect" in lower


def transport_failure(
    response: httpx.Response | None,
    error: str | None,
    latency_ms: int,
    secret: str,
) -> ProviderResponse | None:
    """Kegagalan transport atau HTTP. None berarti body sukses boleh dibaca."""
    if error is not None or response is None:
        message = error or "connection failed"
        kind = FailureKind.NO_RESPONSE if _no_response(message) else FailureKind.PROVIDER_ERROR
        return ProviderResponse(
            text="",
            model_version="",
            latency_ms=latency_ms,
            raw={},
            error=clip(message, secret),
            failure_kind=kind,
        )
    if 200 <= response.status_code < 300:
        return None
    raw = _raw_from_response(response, secret)
    input_tokens, output_tokens = _tokens_from_body(raw)
    return ProviderResponse(
        text="",
        model_version="",
        latency_ms=latency_ms,
        raw=raw,
        error=http_failure(response, secret),
        failure_kind=_http_failure_kind(response.status_code),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _invalid(latency_ms: int, secret: str, raw: dict) -> ProviderResponse:
    return ProviderResponse(
        text="",
        model_version="",
        latency_ms=latency_ms,
        raw=raw,
        error=clip("provider response was not a valid success body", secret),
        failure_kind=FailureKind.PROVIDER_ERROR,
    )


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _usage_pair(payload: dict, input_key: str, output_key: str) -> tuple[int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return _count(usage.get(input_key)), _count(usage.get(output_key))


def _tokens_from_body(payload: dict) -> tuple[int | None, int | None]:
    """Ambil usage dari body sukses atau gagal. 0 tetap 0. Field hilang tetap None."""
    for input_key, output_key in (
        ("total_input_tokens", "total_output_tokens"),
        ("prompt_tokens", "completion_tokens"),
        ("input_tokens", "output_tokens"),
    ):
        found = _usage_pair(payload, input_key, output_key)
        if found != (None, None):
            return found
    return None, None


def _done(
    *,
    text: str,
    model_version: str,
    latency_ms: int,
    raw: dict,
    secret: str,
    kind: FailureKind | None,
    error: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        text=scrub(text, secret),
        model_version=model_version,
        latency_ms=latency_ms,
        raw=raw,
        error=None if error is None else clip(error, secret),
        failure_kind=kind,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def interpret_openai(
    payload: dict | None,
    latency_ms: int,
    model_name: str,
    secret: str,
) -> ProviderResponse:
    raw = scrub(payload, secret) if isinstance(payload, dict) else {}
    if not isinstance(payload, dict):
        return _invalid(latency_ms, secret, raw)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return _invalid(latency_ms, secret, raw)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        message = {}
    text = message.get("content") if isinstance(message.get("content"), str) else ""
    finish = choices[0].get("finish_reason")
    refusal = message.get("refusal")
    kind = None
    error = None
    if finish == "content_filter":
        kind = FailureKind.BLOCKED
        error = "blocked by safety filter"
    elif isinstance(refusal, str) and refusal.strip():
        kind = FailureKind.REFUSED
        error = refusal
    elif finish == "length":
        kind = FailureKind.TRUNCATED
        error = _TRUNCATED
    elif not text.strip():
        kind = FailureKind.EMPTY
        error = _EMPTY
    model = payload.get("model")
    input_tokens, output_tokens = _usage_pair(payload, "prompt_tokens", "completion_tokens")
    return _done(
        text=text,
        model_version=model if isinstance(model, str) else model_name,
        latency_ms=latency_ms,
        raw=raw,
        secret=secret,
        kind=kind,
        error=error,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _text_blocks(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            parts.append(block["text"])
    return parts


def _steps_text(payload: dict) -> str:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return ""
    parts: list[str] = []
    for step in steps:
        if isinstance(step, dict) and step.get("type") == "model_output":
            parts.extend(_text_blocks(step.get("content")))
    return "".join(parts)


def _legacy_gemini_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = payload.get("output")
    if isinstance(output, str):
        return output
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return ""
    parts = [
        item["text"]
        for item in outputs
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    return "".join(parts)


def _gemini_text(payload: dict) -> str:
    from_steps = _steps_text(payload)
    if from_steps:
        return from_steps
    return _legacy_gemini_text(payload)


_GEMINI_STATUSES = frozenset({"completed", "incomplete", "failed"})
_BLOCKED_REASONS = frozenset({"SAFETY", "BLOCKED", "PROHIBITED_CONTENT"})
_REFUSAL_REASONS = frozenset({"refusal", "refused"})
_LENGTH_REASONS = frozenset({"MAX_TOKENS", "LENGTH"})


def _blocked() -> tuple[FailureKind, str]:
    return FailureKind.BLOCKED, "blocked by safety filter"


def _from_status(status: str, text: str) -> tuple[FailureKind | None, str | None]:
    if status == "failed":
        return FailureKind.PROVIDER_ERROR, "provider failed"
    if status == "incomplete":
        return FailureKind.TRUNCATED, _TRUNCATED
    if not text.strip():
        return FailureKind.EMPTY, _EMPTY
    return None, None


def _from_reason(reason: str, text: str) -> tuple[FailureKind | None, str | None]:
    if reason.upper() in _BLOCKED_REASONS:
        return _blocked()
    if reason.lower() in _REFUSAL_REASONS:
        return FailureKind.REFUSED, "model refused to answer"
    if reason.upper() in _LENGTH_REASONS:
        return FailureKind.TRUNCATED, _TRUNCATED
    if not text.strip():
        return FailureKind.EMPTY, _EMPTY
    return None, None


def _gemini_outcome(payload: dict, text: str) -> tuple[FailureKind | None, str | None]:
    if payload.get("block_reason"):
        return _blocked()
    status = payload.get("status")
    if isinstance(status, str) and status in _GEMINI_STATUSES:
        return _from_status(status, text)
    reason = str(payload.get("finish_reason") or payload.get("stop_reason") or "")
    return _from_reason(reason, text)


def interpret_gemini(
    payload: dict | None,
    latency_ms: int,
    model_name: str,
    secret: str,
) -> ProviderResponse:
    raw = scrub(payload, secret) if isinstance(payload, dict) else {}
    if not isinstance(payload, dict):
        return _invalid(latency_ms, secret, raw)
    text = _gemini_text(payload)
    kind, error = _gemini_outcome(payload, text)
    model = payload.get("model")
    input_tokens, output_tokens = _usage_pair(payload, "total_input_tokens", "total_output_tokens")
    return _done(
        text=text,
        model_version=model if isinstance(model, str) else model_name,
        latency_ms=latency_ms,
        raw=raw,
        secret=secret,
        kind=kind,
        error=error,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def interpret_anthropic(
    payload: dict | None,
    latency_ms: int,
    model_name: str,
    secret: str,
) -> ProviderResponse:
    raw = scrub(payload, secret) if isinstance(payload, dict) else {}
    if not isinstance(payload, dict):
        return _invalid(latency_ms, secret, raw)
    content = payload.get("content")
    if not isinstance(content, list):
        return _invalid(latency_ms, secret, raw)
    text = "".join(_text_blocks(content))
    stop = str(payload.get("stop_reason") or "")
    kind = None
    error = None
    if stop == "max_tokens":
        kind = FailureKind.TRUNCATED
        error = _TRUNCATED
    elif stop == "refusal":
        kind = FailureKind.REFUSED
        error = "model refused to answer"
    elif not text.strip():
        kind = FailureKind.EMPTY
        error = _EMPTY
    model = payload.get("model")
    input_tokens, output_tokens = _usage_pair(payload, "input_tokens", "output_tokens")
    return _done(
        text=text,
        model_version=model if isinstance(model, str) else model_name,
        latency_ms=latency_ms,
        raw=raw,
        secret=secret,
        kind=kind,
        error=error,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
