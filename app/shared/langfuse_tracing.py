"""Jejak Langfuse untuk panggilan LLM.

Kunci kosong, URL kosong, atau host Langfuse Cloud berarti tidak ada klien
dan tidak ada jaringan. Kesalahan Langfuse ditelan. Jawaban penyedia tidak berubah.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

from app.modules.providers.usage import TokenUsage
from app.modules.runs.call_cost import CacheRates, actual_cost_usd
from app.shared.config import get_settings

_client = None
_prices: ContextVar["TracePrices | None"] = ContextVar("langfuse_trace_prices", default=None)


@dataclass(frozen=True)
class TracePrices:
    """Harga USD per juta token dari produk yang sudah tersimpan. Bukan daftar harga kedua."""

    usd_per_million_input: Decimal | None
    usd_per_million_output: Decimal | None
    cache_rates: CacheRates | None = None


def client():
    """Klien Langfuse, atau None bila konfigurasi tidak lengkap atau menunjuk ke cloud."""
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    public_key = settings.langfuse_public_key.strip()
    secret_key = settings.langfuse_secret_key.strip()
    base_url = settings.langfuse_base_url.strip()
    if not public_key or not secret_key or not base_url or cloud_host(base_url):
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            environment=settings.app_env,
        )
    except Exception:
        return None
    return _client


def reset_client() -> None:
    """Buang klien yang di-cache. Dipakai tes yang mengganti konfigurasi."""
    global _client
    _client = None


def cloud_host(base_url: str) -> bool:
    """True bila host adalah Langfuse Cloud. Data tidak boleh dikirim ke sana."""
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    host = (urlparse(base_url.strip()).hostname or "").lower().rstrip(".")
    return host == "langfuse.com" or host.endswith(".langfuse.com")


def configured_base_url() -> str | None:
    """URL lokal yang boleh dibuka, atau None bila kosong atau cloud."""
    base_url = get_settings().langfuse_base_url.strip()
    if not base_url or cloud_host(base_url):
        return None
    return base_url.rstrip("/")


@contextmanager
def use_trace_prices(prices: TracePrices | None) -> Iterator[None]:
    """Harga untuk generasi yang terbuka di dalam blok ini."""
    if prices is None:
        yield
        return
    token = _prices.set(prices)
    try:
        yield
    finally:
        _prices.reset(token)


def _quiet(closer) -> None:
    if closer is None:
        return
    with suppress(Exception):
        closer.__exit__(None, None, None)


def flush() -> None:
    langfuse = client()
    if langfuse is None:
        return
    with suppress(Exception):
        langfuse.flush()


def provider_host(base_url: str | None) -> str | None:
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    return urlparse(base_url).hostname


def attempt_output(text: str, failure_kind: str | None) -> str:
    """Teks model, atau jenis kegagalan bila panggilan tidak berhasil."""
    if failure_kind:
        return str(failure_kind)
    return text


class _Output:
    def __init__(self) -> None:
        self.value: str | None = None
        self.failure_kind: str | None = None
        self._scored = False

    def set_output(self, value: str) -> None:
        self.value = value

    def set_outcome(self, text: str, failure_kind: str | None, *, score: bool = True) -> None:
        """Simpan teks atau jenis kegagalan. Skor hanya untuk percobaan pengukuran."""
        kind = None if failure_kind is None else str(failure_kind)
        self.value = attempt_output(text, kind)
        self.failure_kind = kind
        self._scored = score


class _Generation:
    def __init__(self) -> None:
        self.output: str | None = None
        self.usage: dict[str, int] | None = None
        self.cost_usd: Decimal | None = None

    def complete(
        self,
        *,
        output: str,
        input_tokens: int | None,
        output_tokens: int | None,
        usage: TokenUsage | None = None,
    ) -> None:
        self.output = output
        counts: dict[str, int] = {}
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
            counts["input"] = input_tokens
        if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
            counts["output"] = output_tokens
        self.usage = counts or None
        self.cost_usd = _trace_cost(input_tokens, output_tokens, usage)


@contextmanager
def score_case(
    *,
    session_id: str,
    user_id: str | None,
    prompt: str,
    case_index: int,
    repeat_index: int,
    provider_host: str | None,
) -> Iterator[_Output]:
    """Span akar score-case. Tanpa klien, blok ini hanya menjalankan isinya."""
    output = _Output()
    opened = _open_span(
        name="score-case",
        session_id=session_id,
        user_id=user_id,
        prompt=prompt,
        metadata=_metadata(case_index, repeat_index, provider_host),
    )
    try:
        yield output
    finally:
        _close_span(opened, output)


@contextmanager
def judge_call(
    *,
    session_id: str | None,
    user_id: str | None,
    prompt: str,
    kind: str,
) -> Iterator[_Output]:
    """Span induk satu panggilan penilai, supaya ask-provider merekam prompt dan jawabannya."""
    output = _Output()
    metadata = {"kind": kind}
    opened = _open_span(
        name="judge-call",
        session_id=session_id,
        user_id=user_id,
        prompt=prompt,
        metadata=metadata,
    )
    try:
        yield output
    finally:
        _close_span(opened, output)


@contextmanager
def ask_generation(*, model_name: str, prompt: str) -> Iterator[_Generation]:
    """Generasi ask-provider. Hanya dibuka bila span pengukuran atau penilai sedang aktif."""
    generation = _Generation()
    opened = _open_generation(model_name, prompt)
    try:
        yield generation
    finally:
        _close_generation(opened, generation)


def _metadata(case_index: int, repeat_index: int, provider_host_name: str | None) -> dict[str, str]:
    metadata = {
        "case_index": str(case_index),
        "repeat_index": str(repeat_index),
    }
    if provider_host_name:
        metadata["provider_host"] = provider_host_name
    return metadata


def _open_span(
    *,
    name: str,
    session_id: str | None,
    user_id: str | None,
    prompt: str,
    metadata: dict[str, str],
):
    langfuse = client()
    if langfuse is None:
        return None
    attr_cm = None
    try:
        from langfuse import propagate_attributes

        attributes: dict = {
            "environment": get_settings().app_env,
            "metadata": metadata,
        }
        if session_id:
            attributes["session_id"] = session_id
        if user_id:
            attributes["user_id"] = user_id
        attr_cm = propagate_attributes(**attributes)
        attr_cm.__enter__()
        span_cm = langfuse.start_as_current_observation(
            as_type="span",
            name=name,
            input=prompt,
            metadata=metadata,
        )
        span = span_cm.__enter__()
    except Exception:
        _quiet(attr_cm)
        return None
    return span, span_cm, attr_cm


def _close_span(opened, output: _Output) -> None:
    if opened is None:
        return
    span, span_cm, attr_cm = opened
    with suppress(Exception):
        if output.value is not None:
            span.update(output=output.value)
    _record_scores(span, output)
    _quiet(span_cm)
    _quiet(attr_cm)


def _record_scores(span, output: _Output) -> None:
    """Skor jejak dari failure_kind yang sudah dihitung adapter. Bukan nilai hukum."""
    if not output._scored:
        return
    label = output.failure_kind or "ok"
    trace_id = getattr(span, "trace_id", None)
    ok_id = _score_id("attempt_ok", trace_id)
    kind_id = _score_id("failure_kind", trace_id)
    with suppress(Exception):
        span.score_trace(
            name="attempt_ok",
            value=0.0 if output.failure_kind else 1.0,
            data_type="BOOLEAN",
            comment=label,
            score_id=ok_id,
        )
    with suppress(Exception):
        span.score_trace(
            name="failure_kind",
            value=label,
            data_type="CATEGORICAL",
            score_id=kind_id,
        )


def _score_id(name: str, trace_id: object) -> str | None:
    if not isinstance(trace_id, str) or not trace_id:
        return None
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"indolegalbench:{name}:{trace_id}"))


def _open_generation(model_name: str, prompt: str):
    langfuse = client()
    if langfuse is None:
        return None
    try:
        if langfuse.get_current_observation_id() is None:
            return None
        generation_cm = langfuse.start_as_current_observation(
            as_type="generation",
            name="ask-provider",
            model=model_name,
            input=prompt,
        )
        generation = generation_cm.__enter__()
    except Exception:
        return None
    return generation, generation_cm


def _close_generation(opened, generation: _Generation) -> None:
    if opened is None:
        return
    handle, generation_cm = opened
    with suppress(Exception):
        payload: dict = {}
        if generation.output is not None:
            payload["output"] = generation.output
        if generation.usage:
            payload["usage_details"] = generation.usage
        if generation.cost_usd is not None:
            payload["cost_details"] = {"total": float(generation.cost_usd)}
        if payload:
            handle.update(**payload)
    _quiet(generation_cm)


def _trace_cost(
    input_tokens: int | None,
    output_tokens: int | None,
    usage: TokenUsage | None,
) -> Decimal | None:
    prices = _prices.get()
    if prices is None:
        return None
    try:
        return actual_cost_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd_per_million_input_tokens=prices.usd_per_million_input,
            usd_per_million_output_tokens=prices.usd_per_million_output,
            usage=usage,
            cache_rates=prices.cache_rates,
        )
    except Exception:
        return None
