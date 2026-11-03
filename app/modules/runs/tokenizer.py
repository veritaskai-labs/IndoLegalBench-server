"""Penghitung token lokal untuk perkiraan biaya (PBI-11 AC5, SCRUM-244).

Encoding OpenAI cl100k_base dipakai untuk setiap jenis penyedia. Gemini,
Anthropic, dan DeepSeek (openai_compatible) berbagi encoding yang sama.
Berkas encoding dibaca dari cache lokal. Image mengisinya saat build lewat
TIKTOKEN_CACHE_DIR. Gagal memuat berkas itu adalah InternalError, bukan
panggilan ke penyedia dan bukan kunci API. Penanda khusus di dalam
pertanyaan dihitung sebagai teks biasa.
"""

import logging
from typing import Protocol

import tiktoken

from app.shared.exceptions import InternalError

logger = logging.getLogger(__name__)

_OPENAI_ENCODING = "cl100k_base"
# Penyedia tanpa tokenizer sendiri memakai encoding OpenAI.
_ENCODING_FOR_PROVIDER = {
    "openai_compatible": _OPENAI_ENCODING,
    "gemini_interactions": _OPENAI_ENCODING,
    "anthropic_messages": _OPENAI_ENCODING,
}


class TokenCounter(Protocol):
    """Jumlah token dari teks pertanyaan. Bukan panggilan ke penyedia."""

    def count(self, text: str, provider_type: str) -> int:
        """Token untuk teks ini. provider_type memilih encoding, bukan jaringan."""
        raise NotImplementedError


class TiktokenCounter:
    """cl100k_base, dimuat sekali per nama encoding."""

    def __init__(self) -> None:
        self._encoders: dict[str, tiktoken.Encoding] = {}

    def count(self, text: str, provider_type: str) -> int:
        name = _ENCODING_FOR_PROVIDER.get(provider_type, _OPENAI_ENCODING)
        return len(self._encoder(name).encode(text, disallowed_special=()))

    def _encoder(self, name: str) -> tiktoken.Encoding:
        encoder = self._encoders.get(name)
        if encoder is None:
            encoder = self._load(name)
            self._encoders[name] = encoder
        return encoder

    def _load(self, name: str) -> tiktoken.Encoding:
        try:
            return tiktoken.get_encoding(name)
        except (OSError, ValueError, ImportError) as exc:
            logger.error("Token encoding %s could not be loaded: %s", name, exc)
            raise InternalError(
                f"Token encoding {name} is not available.",
                code="TOKEN_ENCODING_UNAVAILABLE",
            ) from None
