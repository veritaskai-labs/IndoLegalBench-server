"""Jejak pengukuran dan penilai. Kesalahan Langfuse tidak mengubah percobaan tersimpan."""

import json
import uuid
from contextlib import nullcontext
from decimal import Decimal

import httpx

from app.modules.providers.adapters import http as provider_http
from app.modules.providers.adapters.dispatch import run_connection_test
from app.modules.providers.adapters.openai_compatible import OpenAICompatibleAdapter
from app.modules.runs.call_cost import CacheRates, actual_cost_usd
from app.modules.runs.judge import JudgeInputs, JudgePrices, run_judge
from app.modules.runs.worker import run_session
from app.shared.langfuse_tracing import TracePrices, use_trace_prices
from tests.modules.runs.test_worker import FakeSessionStore

_PROMPT = "Apa akibat hukumnya?"
_ANSWER = "Jawaban penyedia."
_INPUT = Decimal("0.15")
_OUTPUT = Decimal("0.60")
_CACHE_READ = Decimal("0.03")
_KEY = "sk-test-secret-value"  # pragma: allowlist secret


class _Handle:
    def __init__(self, record: dict) -> None:
        self.record = record
        self.trace_id = "trace-1"

    def update(self, **payload) -> None:
        if self.record.get("raise_on_update"):
            raise RuntimeError("langfuse update failed")
        self.record["updates"].append(payload)

    def score_trace(self, **payload) -> None:
        if self.record.get("raise_on_update"):
            raise RuntimeError("langfuse score failed")
        self.record["scores"].append(payload)


class _Opened:
    def __init__(self, store: "FakeLangfuse", record: dict, kind: str) -> None:
        self.store = store
        self.record = record
        self.kind = kind

    def __enter__(self) -> _Handle:
        if self.kind == "span":
            self.store.current = "span"
        return _Handle(self.record)

    def __exit__(self, *args) -> bool:
        if self.kind == "span":
            self.store.current = None
        return False


class FakeLangfuse:
    def __init__(self, *, raise_on_update: bool = False) -> None:
        self.spans: list[dict] = []
        self.generations: list[dict] = []
        self.current: str | None = None
        self.raise_on_update = raise_on_update

    def get_current_observation_id(self) -> str | None:
        return self.current

    def start_as_current_observation(self, *, as_type: str, name: str, **kwargs):
        record = {
            "name": name,
            "kwargs": kwargs,
            "updates": [],
            "scores": [],
            "raise_on_update": self.raise_on_update,
        }
        if as_type == "span":
            self.spans.append(record)
        else:
            self.generations.append(record)
        return _Opened(self, record, as_type)


def _pasang(monkeypatch, fake: FakeLangfuse) -> list[dict]:
    seen: list[dict] = []

    def attributes(**kwargs):
        seen.append(kwargs)
        return nullcontext()

    monkeypatch.setattr("app.shared.langfuse_tracing.client", lambda: fake)
    monkeypatch.setattr("langfuse.propagate_attributes", attributes)
    return seen


def _transport(monkeypatch, answer: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "prompt_cache_hit_tokens": 200,
                    "prompt_cache_miss_tokens": 800,
                },
            },
        )

    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(provider_http.httpx, "Client", factory)


def _adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        base_url="https://api.example.test/v1/chat/completions",
        model_name="deepseek-flash",
        api_key=_KEY,
    )


def test_pengukuran_merekam_prompt_jawaban_dan_usd(monkeypatch):
    """Positif: score-case dan ask-provider menyimpan prompt, jawaban, dan USD dari harga produk."""
    fake = FakeLangfuse()
    seen = _pasang(monkeypatch, fake)
    _transport(monkeypatch, _ANSWER)
    store = FakeSessionStore([_PROMPT])
    session_id = uuid.uuid4()
    prices = TracePrices(
        usd_per_million_input=_INPUT,
        usd_per_million_output=_OUTPUT,
        cache_rates=CacheRates(read=_CACHE_READ),
    )
    with use_trace_prices(prices):
        run_session(
            session_id,
            store,
            _adapter(),
            repeats=1,
            user_id="user-1",
        )

    assert store.attempts[0].text == _ANSWER
    assert store.attempts[0].failure_kind is None
    assert fake.spans[0]["name"] == "score-case"
    assert fake.spans[0]["kwargs"]["input"] == _PROMPT
    assert seen[0]["session_id"] == str(session_id)
    assert seen[0]["user_id"] == "user-1"
    assert {item["name"] for item in fake.spans[0]["scores"]} == {"attempt_ok", "failure_kind"}
    generation = fake.generations[0]
    assert generation["name"] == "ask-provider"
    assert _PROMPT in generation["kwargs"]["input"]
    assert generation["updates"][0]["output"] == _ANSWER
    assert generation["updates"][0]["usage_details"] == {"input": 1000, "output": 500}
    assert generation["updates"][0]["cost_details"]["total"] == float(_expected_usd_from_usage())
    assert _KEY not in json.dumps(generation["kwargs"])
    assert _KEY not in json.dumps(generation["updates"])


def _expected_usd_from_usage() -> Decimal:
    from app.modules.providers.usage import TokenUsage

    usage = TokenUsage(uncached_input=800, cache_read=200, output=500)
    return actual_cost_usd(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        usd_per_million_input_tokens=_INPUT,
        usd_per_million_output_tokens=_OUTPUT,
        usage=usage,
        cache_rates=CacheRates(read=_CACHE_READ),
    )


def test_penilai_merekam_prompt_dan_jawaban(monkeypatch):
    """Positif: tiap panggilan penilai membuka span, dan ask-provider menyimpan prompt serta jawaban."""
    fake = FakeLangfuse()
    seen = _pasang(monkeypatch, fake)
    reply = "CRITERION: a\nMET: yes"
    _transport(monkeypatch, reply)
    prices = JudgePrices(
        usd_per_million_input_tokens=_INPUT,
        usd_per_million_output_tokens=_OUTPUT,
        usd_idr_rate=Decimal("16000"),
        cache_rates=CacheRates(read=_CACHE_READ),
    )
    hasil = run_judge(
        _adapter(),
        JudgeInputs(
            criteria="a",
            ground_truth="benar",
            question=_PROMPT,
            provider_answer="jawaban kasus",
        ),
        repetitions=1,
        prices=prices,
        session_id="sesi-ukur",
        user_id="user-1",
    )

    assert [baris.failure_kind for baris in hasil.rows] == [None, None]
    assert [span["name"] for span in fake.spans] == ["judge-call", "judge-call"]
    assert all(item["session_id"] == "sesi-ukur" for item in seen)
    assert all(span["scores"] == [] for span in fake.spans)
    assert len(fake.generations) == 2
    for generation in fake.generations:
        assert generation["name"] == "ask-provider"
        assert _PROMPT in generation["kwargs"]["input"]
        assert generation["updates"][0]["output"] == reply
        assert generation["updates"][0]["cost_details"]["total"] == float(
            _expected_usd_from_usage()
        )


def test_uji_koneksi_tidak_membuka_generasi(monkeypatch):
    """Negatif: uji koneksi tidak memanggil perform_ask, jadi tidak ada jejak."""
    fake = FakeLangfuse()
    fake.current = "span"
    _pasang(monkeypatch, fake)
    _transport(monkeypatch, "pong")
    run_connection_test(
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1/chat/completions",
        model_name="deepseek-flash",
        api_key=_KEY,
    )
    assert fake.spans == []
    assert fake.generations == []


def test_kesalahan_langfuse_tidak_mengubah_percobaan(monkeypatch):
    """Negatif: update Langfuse yang gagal tidak mengubah teks atau jenis kegagalan yang disimpan."""
    _transport(monkeypatch, _ANSWER)
    store_biasa = FakeSessionStore([_PROMPT])
    run_session(uuid.uuid4(), store_biasa, _adapter(), repeats=1)

    fake = FakeLangfuse(raise_on_update=True)
    _pasang(monkeypatch, fake)
    store = FakeSessionStore([_PROMPT])
    run_session(uuid.uuid4(), store, _adapter(), repeats=1)

    assert store.attempts[0].text == store_biasa.attempts[0].text == _ANSWER
    assert store.attempts[0].failure_kind == store_biasa.attempts[0].failure_kind
    assert store.attempts[0].error == store_biasa.attempts[0].error
