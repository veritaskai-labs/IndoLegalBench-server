"""Penghitung token lokal untuk perkiraan biaya (PBI-11 AC5, SCRUM-244).

Encoding OpenAI cl100k_base dipakai untuk setiap jenis penyedia. Gemini dan
Anthropic tidak punya tokenizer lokal di sini, jadi keduanya jatuh ke
encoding yang sama. Tidak ada panggilan jaringan dan tidak ada kunci API.
"""

from typing import Protocol

import tiktoken

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
        encoder = self._encoders.get(name)
        if encoder is None:
            encoder = tiktoken.get_encoding(name)
            self._encoders[name] = encoder
        return len(encoder.encode(text))
