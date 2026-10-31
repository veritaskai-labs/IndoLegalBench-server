"""Satu pemetaan parameter generik ke body tiap penyedia.

Field kosong (None) tidak dikirim. Field yang tidak didukung penyedia itu
juga tidak dikirim, dan pemanggilan tidak gagal karenanya. Anthropic
Messages menolak body tanpa max_tokens, jadi None memakai konstanta
ANTHROPIC_DEFAULT_MAX_TOKENS. Penyedia lain mengosongkan max_tokens
karena SCRUM-208 tidak membatasi panjang jawaban.
"""

from collections.abc import Callable
from dataclasses import dataclass

ANTHROPIC_DEFAULT_MAX_TOKENS = 8192
ASK_TIMEOUT_SECONDS = 240.0
_EFFORTS = frozenset({"low", "medium", "high"})


@dataclass(frozen=True)
class ModelParameters:
    """Parameter model dari salinan sesi. None berarti jangan kirim."""

    temperature: float | None = None
    effort: str | None = None
    thinking: bool | None = None
    web_search: bool | None = None
    max_tokens: int | None = None


def _temperature(value: float) -> dict:
    return {"temperature": value}


def _openai_effort(value: str) -> dict | None:
    if value not in _EFFORTS:
        return None
    return {"reasoning_effort": value}


def _max_tokens(value: int) -> dict:
    return {"max_tokens": value}


def _gemini_temperature(value: float) -> dict:
    return {"generation_config": {"temperature": value}}


def _gemini_max_tokens(value: int) -> dict:
    return {"generation_config": {"max_output_tokens": value}}


# thinking dan web_search tidak ada di tabel: ketiga API ini tidak punya
# field yang setara dan aman untuk bool generik, jadi keduanya dijatuhkan.
# Gemini temperature masuk generation_config. Probe live 200 untuk 0.3.
_RENDERERS: dict[str, dict[str, Callable]] = {
    "openai_compatible": {
        "temperature": _temperature,
        "effort": _openai_effort,
        "max_tokens": _max_tokens,
    },
    "gemini_interactions": {
        "temperature": _gemini_temperature,
        "max_tokens": _gemini_max_tokens,
    },
    "anthropic_messages": {
        "temperature": _temperature,
        "max_tokens": _max_tokens,
    },
}


def build_request_extras(provider_type: str, params: ModelParameters | None) -> dict:
    """Kembalikan potongan body. Provider tak dikenal menghasilkan dict kosong."""
    spec = _RENDERERS.get(provider_type)
    if spec is None:
        return {}
    source = params or ModelParameters()
    extras: dict = {}
    for field_name, render in spec.items():
        value = getattr(source, field_name)
        if value is None:
            continue
        rendered = render(value)
        if rendered is None:
            continue
        for key, piece in rendered.items():
            current = extras.get(key)
            if isinstance(current, dict) and isinstance(piece, dict):
                current.update(piece)
            else:
                extras[key] = piece
    if provider_type == "anthropic_messages" and "max_tokens" not in extras:
        extras["max_tokens"] = ANTHROPIC_DEFAULT_MAX_TOKENS
    return extras
