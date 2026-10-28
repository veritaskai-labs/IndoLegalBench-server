"""Port sesi dan antrean untuk worker pengukuran.

SCRUM-205 (tabel) dan SCRUM-207 (antrean) belum ada. Worker hanya
berbicara lewat port ini. Implementasi nyata menyusul di tiket itu.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


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

    def mark_status(self, session_id: uuid.UUID, status: str) -> None:
        """Status sesi: running atau finished."""
        raise NotImplementedError


class QueuePort(Protocol):
    """Antrean sesi. SCRUM-207 yang akan mengisinya."""

    def take_next(self) -> uuid.UUID | None:
        """Ambil satu sesi, atau None kalau antrean kosong."""
        raise NotImplementedError
