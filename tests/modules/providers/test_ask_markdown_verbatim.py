"""SCRUM-76 SCRUM-259: jawaban markdown dari provider keluar dari adapter apa adanya.

Tidak di-trim, tidak di-parse, tidak disanitasi. Satu-satunya yang dibuang adalah
API key provider (scrub), yang bukan bagian dari jawaban.
"""

import httpx
import pytest

from tests.modules.providers.test_ask import _anthropic, _gemini, _openai, _transport

MARKDOWN = (
    "\n## Jawaban\r\n\r\n"
    "**Pesangon** diatur dalam *Pasal 156 UU 13/2003*.\n\n"
    "| Masa kerja | Uang |\n|---|---|\n| < 1 tahun | 1 bulan |\n\n"
    "```python\nprint('§ 156')\n```\n\n"
    "<script>alert('x')</script> <b>tebal</b> [tautan](https://contoh.test)\n\n"
    "- poin ✓ 😀\n\n   \n"
)

_BODIES = {
    "openai": lambda text: {
        "model": "aiyu-1-2026",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
    },
    "gemini": lambda text: {"model": "gemini-2.5-flash", "outputs": [{"text": text}]},
    "anthropic": lambda text: {
        "model": "claude-sonnet-4",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    },
}
_ADAPTERS = {"openai": _openai, "gemini": _gemini, "anthropic": _anthropic}


@pytest.mark.parametrize("kind", ["openai", "gemini", "anthropic"])
def test_adapter_returns_markdown_answer_byte_for_byte(monkeypatch, kind):
    # Arrange
    _transport(monkeypatch, lambda request: httpx.Response(200, json=_BODIES[kind](MARKDOWN)))

    # Act
    result = _ADAPTERS[kind]().ask("tanya")

    # Assert
    assert result.failure_kind is None
    assert result.text == MARKDOWN


def test_adapter_removes_only_the_api_key_from_the_answer(monkeypatch):
    # Arrange
    from tests.modules.providers.test_ask import _KEY

    echoed = f"Kunci Anda {_KEY} tidak boleh tersimpan.\n\n**tebal**"
    _transport(monkeypatch, lambda request: httpx.Response(200, json=_BODIES["openai"](echoed)))

    # Act
    result = _openai().ask("tanya")

    # Assert
    assert result.text == "Kunci Anda  tidak boleh tersimpan.\n\n**tebal**"
