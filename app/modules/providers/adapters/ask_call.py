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


def transport_failure(
    response: httpx.Response | None,
    error: str | None,
    latency_ms: int,
    secret: str,
) -> ProviderResponse | None:
    """Kegagalan transport atau HTTP. None berarti body sukses boleh dibaca."""
    if error is not None or response is None:
        message = error or "connection failed"
        lower = message.lower()
        if "timed out" in lower or "could not connect" in lower:
            kind = FailureKind.NO_RESPONSE
        else:
            kind = FailureKind.PROVIDER_ERROR
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
    if response.status_code in (401, 403):
        kind = FailureKind.KEY_REJECTED
    elif response.status_code == 404:
        kind = FailureKind.MODEL_NOT_FOUND
    else:
        kind = FailureKind.PROVIDER_ERROR
    return ProviderResponse(
        text="",
        model_version="",
        latency_ms=latency_ms,
        raw=_raw_from_response(response, secret),
        error=http_failure(response, secret),
        failure_kind=kind,
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


def _done(
    *,
    text: str,
    model_version: str,
    latency_ms: int,
    raw: dict,
    secret: str,
    kind: FailureKind | None,
    error: str | None,
) -> ProviderResponse:
    return ProviderResponse(
        text=scrub(text, secret),
        model_version=model_version,
        latency_ms=latency_ms,
        raw=raw,
        error=None if error is None else clip(error, secret),
        failure_kind=kind,
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
    return _done(
        text=text,
        model_version=model if isinstance(model, str) else model_name,
        latency_ms=latency_ms,
        raw=raw,
        secret=secret,
        kind=kind,
        error=error,
    )


def _gemini_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = payload.get("output")
    if isinstance(output, str):
        return output
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return ""
    parts: list[str] = []
    for item in outputs:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


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
    reason = str(payload.get("finish_reason") or payload.get("stop_reason") or "")
    kind = None
    error = None
    if payload.get("block_reason") or reason.upper() in {"SAFETY", "BLOCKED", "PROHIBITED_CONTENT"}:
        kind = FailureKind.BLOCKED
        error = "blocked by safety filter"
    elif reason.lower() in {"refusal", "refused"}:
        kind = FailureKind.REFUSED
        error = "model refused to answer"
    elif reason.upper() in {"MAX_TOKENS", "LENGTH"}:
        kind = FailureKind.TRUNCATED
        error = _TRUNCATED
    elif not text.strip():
        kind = FailureKind.EMPTY
        error = _EMPTY
    model = payload.get("model")
    return _done(
        text=text,
        model_version=model if isinstance(model, str) else model_name,
        latency_ms=latency_ms,
        raw=raw,
        secret=secret,
        kind=kind,
        error=error,
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
    parts: list[str] = []
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            parts.append(block["text"])
    text = "".join(parts)
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
    return _done(
        text=text,
        model_version=model if isinstance(model, str) else model_name,
        latency_ms=latency_ms,
        raw=raw,
        secret=secret,
        kind=kind,
        error=error,
    )
