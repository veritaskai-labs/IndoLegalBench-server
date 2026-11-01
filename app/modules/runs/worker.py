"""Loop worker pengukuran. Tidak mengenal HTTP dan tidak mengenal tabel.

SCRUM-76 SCRUM-208. Satu sesi, tiap kasus diulang, kegagalan dicatat
lalu loop lanjut. Tidak ada retry otomatis.
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.modules.providers.adapters.base import FailureKind, ProviderResponse
from app.modules.providers.adapters.params import ASK_TIMEOUT_SECONDS, ModelParameters
from app.modules.runs.ports import AttemptRecord, QueuePort, SessionStore
from app.shared.exceptions import ValidationError

DEFAULT_REPEATS = 3
MIN_REPEATS = 1
MAX_REPEATS = 5


class _Asker(Protocol):
    def ask(
        self,
        prompt: str,
        *,
        parameters: ModelParameters | None = None,
        timeout: float = ASK_TIMEOUT_SECONDS,
    ) -> ProviderResponse: ...


def run_session(
    session_id: uuid.UUID,
    store: SessionStore,
    adapter: _Asker,
    params: ModelParameters | None = None,
    repeats: int = DEFAULT_REPEATS,
    rate_limit_per_minute: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    call_log=None,
    product_id: uuid.UUID | None = None,
) -> None:
    """Jalankan setiap kasus × repeats. repeats di luar 1–5 ditolak.

    rate_limit_per_minute None berarti jangan menunggu. Angka positif
    menahan awal panggilan berikutnya minimal 60/limit detik dari awal
    panggilan sebelumnya, supaya tidak ada burst.
    """
    if repeats < MIN_REPEATS or repeats > MAX_REPEATS:
        raise ValidationError(
            "repeats must be between 1 and 5",
            field="repeats",
        )
    interval = _call_interval(rate_limit_per_minute)
    prompts = list(store.list_cases(session_id))
    store.mark_status(session_id, "running")
    _run_prompts(
        session_id,
        store,
        adapter,
        params,
        prompts,
        repeats,
        _Pace(interval, sleep, clock, call_log, product_id),
    )
    store.mark_status(session_id, "finished")


@dataclass(frozen=True)
class _Pace:
    interval: float | None
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    call_log: object
    product_id: uuid.UUID | None


def _run_prompts(
    session_id: uuid.UUID,
    store: SessionStore,
    adapter: _Asker,
    params: ModelParameters | None,
    prompts: list[str],
    repeats: int,
    pace: _Pace,
) -> None:
    completed = 0
    failed = 0
    next_start: float | None = None
    for case_index, prompt in enumerate(prompts):
        completed, failed, next_start = _run_repeats(
            session_id,
            store,
            adapter,
            params,
            case_index,
            prompt,
            repeats,
            pace,
            completed,
            failed,
            next_start,
        )


def _run_repeats(
    session_id: uuid.UUID,
    store: SessionStore,
    adapter: _Asker,
    params: ModelParameters | None,
    case_index: int,
    prompt: str,
    repeats: int,
    pace: _Pace,
    completed: int,
    failed: int,
    next_start: float | None,
) -> tuple[int, int, float | None]:
    for repeat_index in range(repeats):
        if _over_budget(pace.call_log, pace.product_id):
            break
        _wait_until(next_start, pace.clock, pace.sleep)
        started = pace.clock()
        started_at = datetime.now(UTC)
        response = _ask(adapter, prompt, params)
        ended_at = datetime.now(UTC)
        if pace.interval is not None:
            next_start = started + pace.interval
        failed_call = response.failure_kind is not None or response.error is not None
        if failed_call:
            failed += 1
        else:
            completed += 1
        saved = _save(
            session_id, store, case_index, repeat_index, prompt, response, completed, failed
        )
        if pace.call_log is not None and pace.product_id is not None:
            pace.call_log.record(pace.product_id, saved, started_at, ended_at)
    return completed, failed, next_start


def _over_budget(call_log, product_id: uuid.UUID | None) -> bool:
    if call_log is None or product_id is None:
        return False
    return not call_log.allows(product_id)


def _save(
    session_id: uuid.UUID,
    store: SessionStore,
    case_index: int,
    repeat_index: int,
    prompt: str,
    response: ProviderResponse,
    completed: int,
    failed: int,
) -> AttemptRecord:
    kind = response.failure_kind
    record = AttemptRecord(
        session_id=session_id,
        case_index=case_index,
        repeat_index=repeat_index,
        prompt=prompt,
        text=response.text,
        model_version=response.model_version,
        latency_ms=response.latency_ms,
        raw=response.raw,
        error=response.error,
        failure_kind=None if kind is None else str(kind),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    store.save_attempt(record)
    store.update_progress(session_id, completed=completed, failed=failed)
    return record


def run_next(
    queue: QueuePort,
    store: SessionStore,
    adapter: _Asker,
    params: ModelParameters | None = None,
    repeats: int = DEFAULT_REPEATS,
    rate_limit_per_minute: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Ambil satu sesi dari antrean. False kalau antrean kosong."""
    session_id = queue.take_next()
    if session_id is None:
        return False
    run_session(
        session_id,
        store,
        adapter,
        params,
        repeats,
        rate_limit_per_minute=rate_limit_per_minute,
        sleep=sleep,
        clock=clock,
    )
    return True


def _call_interval(rate_limit_per_minute: int | None) -> float | None:
    if rate_limit_per_minute is None:
        return None
    if rate_limit_per_minute <= 0:
        raise ValidationError(
            "rate_limit_per_minute must be positive",
            field="rate_limit_per_minute",
        )
    return 60.0 / rate_limit_per_minute


def _wait_until(
    earliest: float | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Ulangi tidur sampai jam mencapai earliest, kalau sleep kembali lebih awal."""
    if earliest is None:
        return
    while True:
        delay = earliest - clock()
        if delay <= 0:
            return
        sleep(delay)


def _ask(adapter: _Asker, prompt: str, params: ModelParameters | None) -> ProviderResponse:
    try:
        return adapter.ask(prompt, parameters=params, timeout=ASK_TIMEOUT_SECONDS)
    except Exception as exc:
        return ProviderResponse(
            text="",
            model_version="",
            latency_ms=0,
            raw={},
            error=str(exc),
            failure_kind=FailureKind.PROVIDER_ERROR,
        )
