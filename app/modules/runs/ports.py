"""Port sesi dan antrean untuk worker pengukuran.

Antrean sesi (QueuePort) dipenuhi SessionQueue di runs/service.py (SCRUM-207),
di atas tabel SCRUM-205. SessionStore worker di atas tabel attempts belum ada;
sampai itu ada, worker hanya berbicara lewat port ini.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.runs.models import SessionStatus, StopReason


@dataclass(frozen=True)
class AttemptRecord:
    """Satu percobaan. raw adalah bukti respons penyedia, sudah tanpa rahasia."""

    session_id: uuid.UUID
    case_index: int
    repeat_index: int
    prompt: str
    text: str
    model_version: str
    latency_ms: int
    raw: dict
    error: str | None
    failure_kind: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    uncached_input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_write_long_tokens: int | None = None
    attempt_id: uuid.UUID | None = None
    # Saat respons diterima atau gagal. None untuk store lama; store tabel mengisi saat simpan.
    ended_at: datetime | None = None


class SessionStore(Protocol):
    """Penyimpanan sesi. Bukan tabel. Tes menyuntik penyimpanan palsu."""

    def list_cases(self, session_id: uuid.UUID) -> Sequence[str]:
        """Teks pertanyaan apa adanya, urutan kasus dalam sesi."""
        raise NotImplementedError

    def save_attempt(self, record: AttemptRecord) -> None:
        """Simpan jawaban mentah atau kegagalan teknis."""
        raise NotImplementedError

    def update_progress(self, session_id: uuid.UUID, *, completed: int, failed: int) -> None:
        """Jumlah selesai, jumlah gagal, dipanggil setelah setiap percobaan."""
        raise NotImplementedError

    def mark_status(
        self,
        session_id: uuid.UUID,
        status: SessionStatus,
        *,
        stop_reason: StopReason | None = None,
    ) -> None:
        """Status akhir sesi (D4): finished, finished_with_failures, atau paused + penyebab.

        Implementasi SQL memanggil SessionStatusRepository.change_status (SCRUM-214).
        """
        raise NotImplementedError


class QueuePort(Protocol):
    """Antrean sesi. SCRUM-207 yang akan mengisinya."""

    def take_next(self) -> uuid.UUID | None:
        """Ambil satu sesi, atau None kalau antrean kosong."""
        raise NotImplementedError
