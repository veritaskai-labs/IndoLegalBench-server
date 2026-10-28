"""Loop worker pengukuran. Tidak mengenal HTTP dan tidak mengenal tabel.

SCRUM-76 SCRUM-208. Satu sesi, tiap kasus diulang, kegagalan dicatat
lalu loop lanjut. Tidak ada retry otomatis.
"""

import uuid
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
) -> None:
    """Jalankan setiap kasus × repeats. repeats di luar 1–5 ditolak."""
    if repeats < MIN_REPEATS or repeats > MAX_REPEATS:
        raise ValidationError(
            "repeats must be between 1 and 5",
            field="repeats",
        )
    prompts = list(store.list_cases(session_id))
    store.mark_status(session_id, "running")
    completed = 0
    failed = 0
    for case_index, prompt in enumerate(prompts):
        for repeat_index in range(repeats):
            response = _ask(adapter, prompt, params)
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
) -> bool:
    """Ambil satu sesi dari antrean. False kalau antrean kosong."""
    session_id = queue.take_next()
    if session_id is None:
        return False
    run_session(session_id, store, adapter, params, repeats)
    return True


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
