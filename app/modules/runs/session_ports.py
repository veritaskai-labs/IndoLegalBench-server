"""Port penyimpanan sesi pengukuran (PBI-11, SCRUM-207).

Tabel measurement_sessions dan measurement_session_suites (D2) milik SCRUM-205
belum ada. Service hanya berbicara lewat port ini, jadi adapter tabel nanti
cukup memenuhi kontrak yang sama, tanpa mengubah service atau router.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.modules.runs.schemas import CaseFilter, MeasurementPurpose, SessionStatus


@dataclass(frozen=True)
class NewSession:
    """Pilihan pemulai di halaman Buat Pengukuran, yang disimpan bersama sesi."""

    label: str
    purpose: MeasurementPurpose
    product_id: uuid.UUID
    suite_ids: tuple[uuid.UUID, ...]
    case_filter: CaseFilter
    repetitions: int
    created_by: uuid.UUID


@dataclass(frozen=True)
class QueuedSession:
    id: uuid.UUID
    status: SessionStatus
    queue_order: int


class SessionQueueStore(Protocol):
    def create_queued(self, new: NewSession) -> QueuedSession:
        """Simpan sesi berstatus queued dengan queue_order berikutnya (AC3, D3 langkah 21)."""
        raise NotImplementedError
