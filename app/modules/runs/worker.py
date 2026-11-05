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
from app.modules.runs.models import SessionStatus, StopReason
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
    before_attempt: Callable[[int, int], uuid.UUID | None] | None = None,
    after_attempt: Callable[[], None] | None = None,
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
    # Tidak menulis running: SessionQueue.start() sudah memindahkan sesi ke running,
    # dan running -> running ditolak tabel D4 (SCRUM-214).
    stopped, failed = _run_prompts(
        session_id,
        store,
        adapter,
        params,
        prompts,
        repeats,
        _Pace(interval, sleep, clock, call_log, product_id, before_attempt, after_attempt),
    )
    if stopped:
        # Anggaran habis: hasil yang selesai tersimpan, sesi bisa dilanjutkan (Adra, 4 Nov).
        store.mark_status(session_id, SessionStatus.PAUSED, stop_reason=StopReason.BUDGET)
    elif failed:
        store.mark_status(session_id, SessionStatus.FINISHED_WITH_FAILURES)
    else:
        store.mark_status(session_id, SessionStatus.FINISHED)


@dataclass(frozen=True)
class _Pace:
    interval: float | None
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    call_log: object
    product_id: uuid.UUID | None
    before_attempt: Callable[[int, int], uuid.UUID | None] | None
    after_attempt: Callable[[], None] | None


def _run_prompts(
    session_id: uuid.UUID,
    store: SessionStore,
    adapter: _Asker,
    params: ModelParameters | None,
    prompts: list[str],
    repeats: int,
    pace: _Pace,
) -> tuple[bool, int]:
    """(berhenti karena anggaran, jumlah percobaan gagal)."""
    completed = 0
    failed = 0
    next_start: float | None = None
    stopped = False
    for case_index, prompt in enumerate(prompts):
        completed, failed, next_start, stopped = _run_repeats(
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
        if stopped:
            break
    return stopped, failed


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
) -> tuple[int, int, float | None, bool]:
    stopped = False
    for repeat_index in range(repeats):
        if _over_budget(pace.call_log, pace.product_id):
            stopped = True
            break
        _wait_until(next_start, pace.clock, pace.sleep)
        attempt_id = None
        if pace.before_attempt is not None:
            attempt_id = pace.before_attempt(case_index, repeat_index)
            if attempt_id is None:
                continue
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
        record = _attempt_record(session_id, case_index, repeat_index, prompt, response, attempt_id)
        rate = _bill_before_save(pace, record)
        _save(session_id, store, record, completed, failed)
        _record_call(pace, record, started_at, ended_at, rate)
        if pace.after_attempt is not None:
            pace.after_attempt()
    return completed, failed, next_start, stopped


def _over_budget(call_log, product_id: uuid.UUID | None) -> bool:
    if call_log is None or product_id is None:
        return False
    return not call_log.allows(product_id)


def _bill_before_save(pace: _Pace, record: AttemptRecord):
    """None bila sesi ini tidak mencatat. Selain itu kurs yang sudah lolos cek."""
    if pace.call_log is None or pace.product_id is None:
        return None
    return pace.call_log.ensure_billable(pace.product_id, record)


def _record_call(pace: _Pace, record: AttemptRecord, started_at, ended_at, rate) -> None:
    if pace.call_log is None or pace.product_id is None:
        return
    pace.call_log.record(
        pace.product_id,
        record,
        started_at,
        ended_at,
        usd_idr_rate=rate,
    )


def _attempt_record(
    session_id: uuid.UUID,
    case_index: int,
    repeat_index: int,
    prompt: str,
    response: ProviderResponse,
    attempt_id: uuid.UUID | None = None,
) -> AttemptRecord:
    kind = response.failure_kind
    return AttemptRecord(
        session_id=session_id,
        attempt_id=attempt_id,
        case_index=case_index,
        repeat_index=repeat_index,
        prompt=prompt,
        text=response.text,
        model_version=response.model_version,
        latency_ms=response.latency_ms,
        raw=response.raw
        if attempt_id is None
        else {
            **response.raw,
            "_execution": {"error": response.error, "answer_text": response.text},
        },
        error=response.error,
        failure_kind=None if kind is None else str(kind),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )


def _save(
    session_id: uuid.UUID,
    store: SessionStore,
    record: AttemptRecord,
    completed: int,
    failed: int,
) -> None:
    store.save_attempt(record)
    store.update_progress(session_id, completed=completed, failed=failed)


def run_next(
    queue: QueuePort,
    store: SessionStore,
    adapter: _Asker,
    params: ModelParameters | None = None,
    repeats: int = DEFAULT_REPEATS,
    rate_limit_per_minute: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    call_log=None,
    product_id: uuid.UUID | None = None,
    before_attempt: Callable[[int, int], uuid.UUID | None] | None = None,
    after_attempt: Callable[[], None] | None = None,
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
        call_log=call_log,
        product_id=product_id,
        before_attempt=before_attempt,
        after_attempt=after_attempt,
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
