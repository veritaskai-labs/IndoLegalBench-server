"""Loop worker pengukuran. Tidak mengenal HTTP dan tidak mengenal tabel.

SCRUM-76 SCRUM-208. Satu sesi, tiap kasus diulang, kegagalan dicatat
lalu loop lanjut. Tidak ada retry otomatis.
"""

import time
import uuid
from collections.abc import Callable
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
    completed = 0
    failed = 0
    next_start: float | None = None
    for case_index, prompt in enumerate(prompts):
        for repeat_index in range(repeats):
            _wait_until(next_start, clock, sleep)
            started = clock()
            response = _ask(adapter, prompt, params)
            if interval is not None:
                next_start = started + interval
            kind = response.failure_kind
            if kind is not None or response.error is not None:
                failed += 1
            else:
                completed += 1
            store.save_attempt(
                AttemptRecord(
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
                )
            )
            store.update_progress(session_id, completed=completed, failed=failed)
    store.mark_status(session_id, "finished")


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
