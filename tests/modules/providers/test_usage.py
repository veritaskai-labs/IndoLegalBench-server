"""Usage token dari respons penyedia yang di-stub. Tidak ada panggilan jaringan.

HTTP penyedia diganti MockTransport, sama seperti tes ask() yang lain. DeepSeek
diuji lewat adapter openai_compatible karena bentuk Chat Completions-nya sama
dengan OpenAI. Angka di bawah ini stub dari respons yang sudah direkam.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Edge: batas atau kasus pinggir.
"""

import json

import httpx

from tests.modules.providers.test_ask import _anthropic, _gemini, _openai, _transport

# Stub respons deepseek-flash. Bentuknya OpenAI, ditambah hit dan miss.
# Token penalaran sudah ada di dalam completion.
DEEPSEEK_OPENAI_USAGE = {
    "prompt_tokens": 37,
    "completion_tokens": 13,
    "total_tokens": 50,
    "prompt_tokens_details": {"cached_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 11},
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 37,
}

# Stub respons Gemini Interactions. Token berpikir di luar output.
# total = input + output + thoughts + tool_use. Token alat tidak ditagih.
GEMINI_INTERACTIONS_USAGE = {
    "total_tokens": 26,
    "total_input_tokens": 8,
    "input_tokens_by_modality": [{"modality": "text", "tokens": 8}],
    "total_cached_tokens": 0,
    "total_output_tokens": 1,
    "total_tool_use_tokens": 0,
    "total_thought_tokens": 17,
    "initial_input_tokens": 8,
}


def _stub(monkeypatch, payload: dict, status: int = 200):
    """Ganti klien HTTP dengan respons tetap. Host di payload tidak pernah dihubungi."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload)

    _transport(monkeypatch, handler, seen)
    return seen


def _buckets(result):
    usage = result.usage
    return (
        usage.uncached_input,
        usage.cache_read,
        usage.cache_write,
        usage.cache_write_long,
        usage.output,
        result.input_tokens,
        result.output_tokens,
    )


def test_openai_stub_keeps_prompt_and_completion(monkeypatch):
    """Positif: tanpa rincian cache, prompt adalah input dan completion adalah output."""
    seen = _stub(
        monkeypatch,
        {
            "model": "aiyu-1",
            "choices": [{"message": {"content": "jawab"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
        },
    )

    result = _openai().ask("q")

    assert seen["url"].startswith("https://api.example.test/")
    assert _buckets(result) == (11, None, None, None, 4, 11, 4)


def test_openai_stub_splits_cache_inside_the_prompt(monkeypatch):
    """Positif: cached dan cache write ada di dalam prompt, bukan tambahan.

    Token penalaran tetap di dalam completion.
    """
    _stub(
        monkeypatch,
        {
            "model": "m",
            "choices": [{"message": {"content": "jawab"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 9,
                "prompt_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 5},
                "completion_tokens_details": {"reasoning_tokens": 4},
            },
        },
    )

    result = _openai().ask("q")

    assert _buckets(result) == (15, 80, 5, None, 9, 100, 9)


def test_deepseek_stub_uses_the_openai_compatible_adapter(monkeypatch):
    """Positif: DeepSeek di-stub sebagai openai_compatible karena formatnya sama.

    Hit dan miss menggantikan cached_tokens, jadi cache tidak dikurangi dua kali.
    Token penalaran 11 tetap di dalam completion 13.
    """
    seen = _stub(
        monkeypatch,
        {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": DEEPSEEK_OPENAI_USAGE,
        },
    )

    result = _openai().ask("q")

    assert seen["url"].startswith("https://api.example.test/")
    assert seen["body"]["model"] == "aiyu-1"
    assert _buckets(result) == (37, 0, None, None, 13, 37, 13)


def test_anthropic_stub_adds_cache_and_splits_write_tiers(monkeypatch):
    """Positif: input_tokens Anthropic tidak memuat cache. Tulis 5 menit dan 1 jam terpisah."""
    _stub(
        monkeypatch,
        {
            "model": "m",
            "content": [{"type": "text", "text": "jawab"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 8,
                "output_tokens": 6,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 5,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 20,
                    "ephemeral_1h_input_tokens": 10,
                },
            },
        },
    )

    result = _anthropic().ask("q")

    assert _buckets(result) == (8, 5, 20, 10, 6, 43, 6)


def test_anthropic_stub_keeps_write_total_when_tiers_are_zero(monkeypatch):
    """Edge: tingkatan 0 padahal jumlah tulis terisi tetap menghitung token tulis itu."""
    _stub(
        monkeypatch,
        {
            "model": "m",
            "content": [{"type": "text", "text": "jawab"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 3,
                "output_tokens": 1,
                "cache_creation_input_tokens": 12,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 0,
                },
            },
        },
    )

    result = _anthropic().ask("q")

    assert _buckets(result) == (3, None, 12, None, 1, 15, 1)


def test_gemini_stub_adds_thoughts_and_ignores_tool_tokens(monkeypatch):
    """Positif: keluaran yang ditagih adalah jawaban ditambah token berpikir.

    Stub Gemini Interactions: 8 input, 1 output, 17 berpikir. Token alat tidak masuk.
    """
    _stub(
        monkeypatch,
        {
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "jawab"}]}],
            "usage": GEMINI_INTERACTIONS_USAGE,
        },
    )

    result = _gemini().ask("q")

    assert _buckets(result) == (8, 0, None, None, 18, 8, 18)

    with_tools = dict(GEMINI_INTERACTIONS_USAGE)
    with_tools["total_tool_use_tokens"] = 9
    _stub(
        monkeypatch,
        {
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "jawab"}]}],
            "usage": with_tools,
        },
    )
    assert _buckets(_gemini().ask("q")) == (8, 0, None, None, 18, 8, 18)


def test_stub_keeps_missing_unset_and_zero_as_zero(monkeypatch):
    """Edge: field yang tidak ada pada stub bukan 0. Angka 0 pada stub tetap 0."""
    _stub(
        monkeypatch,
        {
            "model": "m",
            "choices": [{"message": {"content": "jawab"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2},
        },
    )
    missing = _openai().ask("q")
    assert _buckets(missing) == (2, None, None, None, None, 2, None)

    _stub(
        monkeypatch,
        {
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "jawab"}]}],
            "usage": {
                "total_input_tokens": 0,
                "total_cached_tokens": 0,
                "total_output_tokens": 0,
            },
        },
    )
    zero = _gemini().ask("q")
    assert _buckets(zero) == (0, 0, None, None, 0, 0, 0)
