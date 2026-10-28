"""SCRUM-76 SCRUM-208: worker loop against a fake session store and mock provider."""

import uuid

import pytest

from app.modules.providers.adapters.base import FailureKind, ProviderResponse
from app.modules.providers.adapters.mock_provider import MockProviderAdapter
from app.modules.providers.adapters.params import ModelParameters
from app.modules.runs.ports import AttemptRecord, QueuePort, SessionStore
from app.modules.runs.worker import run_next, run_session
from app.shared.exceptions import ValidationError


class FakeSessionStore:
    def __init__(self, prompts: list[str]) -> None:
        self.prompts = prompts
        self.attempts: list[AttemptRecord] = []
        self.progress: list[tuple[int, int]] = []
        self.statuses: list[str] = []

    def list_cases(self, session_id: uuid.UUID) -> list[str]:
        del session_id
        return list(self.prompts)

    def save_attempt(self, record: AttemptRecord) -> None:
        self.attempts.append(record)

    def update_progress(self, session_id: uuid.UUID, *, completed: int, failed: int) -> None:
        del session_id
        self.progress.append((completed, failed))

    def mark_status(self, session_id: uuid.UUID, status: str) -> None:
        del session_id
        self.statuses.append(status)


class FakeQueue:
    def __init__(self, session_ids: list[uuid.UUID]) -> None:
        self._ids = list(session_ids)

    def take_next(self) -> uuid.UUID | None:
        if not self._ids:
            return None
        return self._ids.pop(0)


class BoomAdapter:
    name = "boom"

    def __init__(self) -> None:
        self.calls = 0

    def ask(self, prompt: str, *, parameters=None, timeout: float = 240.0):
        del parameters, timeout
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("adapter exploded")
        return ProviderResponse(
            text=prompt,
            model_version="boom",
            latency_ms=1,
            raw={},
        )


def test_run_session_repeats_each_case_and_sends_the_raw_prompt():
    session_id = uuid.uuid4()
    store = FakeSessionStore(["pasal 1?", "pasal 2?"])
    run_session(
        session_id,
        store,
        MockProviderAdapter("success"),
        ModelParameters(temperature=0.3),
        repeats=3,
    )
    assert len(store.attempts) == 6
    assert [item.prompt for item in store.attempts] == ["pasal 1?"] * 3 + ["pasal 2?"] * 3
    assert all(item.text == f"mock:{item.prompt}" for item in store.attempts)
    assert all(item.failure_kind is None for item in store.attempts)
    assert store.progress[-1] == (6, 0)
    assert len(store.progress) == 6
    assert store.statuses == ["running", "finished"]


def test_failure_does_not_stop_the_loop():
    session_id = uuid.uuid4()
    store = FakeSessionStore(["satu", "dua"])
    run_session(session_id, store, MockProviderAdapter("error"), repeats=2)
    assert len(store.attempts) == 4
    assert all(item.failure_kind == FailureKind.PROVIDER_ERROR for item in store.attempts)
    assert store.progress[-1] == (0, 4)
    assert store.statuses[-1] == "finished"


@pytest.mark.parametrize("behavior", ["empty", "truncated", "blocked", "refusal", "key_rejected", "model_not_found", "slow"])
def test_each_mock_behavior_is_recorded(behavior):
    store = FakeSessionStore(["q"])
    run_session(uuid.uuid4(), store, MockProviderAdapter(behavior), repeats=1)
    assert len(store.attempts) == 1
    if behavior in ("slow",):
        assert store.attempts[0].failure_kind is None
        assert store.progress[-1] == (1, 0)
    else:
        assert store.attempts[0].failure_kind is not None
        assert store.progress[-1] == (0, 1)


def test_adapter_exception_is_recorded_and_the_next_attempt_runs():
    store = FakeSessionStore(["a", "b"])
    adapter = BoomAdapter()
    run_session(uuid.uuid4(), store, adapter, repeats=1)
    assert adapter.calls == 2
    assert store.attempts[0].failure_kind is None
    assert store.attempts[1].failure_kind == FailureKind.PROVIDER_ERROR
    assert "exploded" in (store.attempts[1].error or "")
    assert store.statuses[-1] == "finished"


def test_empty_session_finishes_without_attempts():
    store = FakeSessionStore([])
    run_session(uuid.uuid4(), store, MockProviderAdapter("success"))
    assert store.attempts == []
    assert store.progress == []
    assert store.statuses == ["running", "finished"]


@pytest.mark.parametrize("repeats", [0, 6, -1])
def test_repeats_outside_1_to_5_are_rejected(repeats):
    store = FakeSessionStore(["q"])
    with pytest.raises(ValidationError):
        run_session(uuid.uuid4(), store, MockProviderAdapter("success"), repeats=repeats)
    assert store.attempts == []


@pytest.mark.parametrize("repeats", [1, 5])
def test_repeats_bounds_are_accepted(repeats):
    store = FakeSessionStore(["q"])
    run_session(uuid.uuid4(), store, MockProviderAdapter("success"), repeats=repeats)
    assert len(store.attempts) == repeats


def test_default_repeats_is_three():
    store = FakeSessionStore(["q"])
    run_session(uuid.uuid4(), store, MockProviderAdapter("success"))
    assert len(store.attempts) == 3


def test_run_next_empty_queue_does_nothing():
    store = FakeSessionStore(["q"])
    assert run_next(FakeQueue([]), store, MockProviderAdapter("success")) is False
    assert store.attempts == []


def test_run_next_takes_one_session():
    session_id = uuid.uuid4()
    store = FakeSessionStore(["q"])
    queue = FakeQueue([session_id])
    assert run_next(queue, store, MockProviderAdapter("success"), repeats=1) is True
    assert store.attempts[0].session_id == session_id
    assert queue.take_next() is None


def test_port_methods_are_not_a_working_store():
    session_id = uuid.uuid4()
    with pytest.raises(NotImplementedError):
        SessionStore.list_cases(None, session_id)
    with pytest.raises(NotImplementedError):
        SessionStore.save_attempt(None, None)
    with pytest.raises(NotImplementedError):
        SessionStore.update_progress(None, session_id, completed=0, failed=0)
    with pytest.raises(NotImplementedError):
        SessionStore.mark_status(None, session_id, "running")
    with pytest.raises(NotImplementedError):
        QueuePort.take_next(None)
