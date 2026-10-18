"""Port penugasan reviewer dan notifikasi.

PBI-6, SCRUM-144. Notifikasi milik SCRUM-153 belum ada, jadi pemanggilan
notify lewat port. Penyimpanan penugasan memakai tabel review lewat
ReviewTableStore di reviews.service. Tes boleh menyuntik penyimpanan palsu.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ReviewerLoad:
    """Beban satu reviewer saat kasus baru diajukan."""

    open_count: int
    # None berarti reviewer belum pernah ditugaskan.
    last_assigned_at: datetime | None
    total_count: int


class AssignmentStore(Protocol):
    """Penyimpanan penugasan review.

    ReviewTableStore di reviews.service memakai review_rounds,
    review_assignments, dan review_verdicts.
    """

    def open_queue(self, reviewer_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, ReviewerLoad]:
        """Beban tiap reviewer. Reviewer tanpa penugasan boleh tidak ada di hasil.

        open_count hanya menghitung penugasan berstatus active yang belum
        dinilai reviewer itu. Penugasan replaced, void, atau yang sudah
        punya verdict tidak dihitung. last_assigned_at dan total_count
        memakai semua penugasan reviewer itu, apa pun statusnya.
        """
        ...

    def save_assignments(self, round_id: uuid.UUID, reviewer_ids: Sequence[uuid.UUID]) -> None:
        """Simpan penugasan active untuk round ini, tanpa commit."""
        ...

    def mark_needs_assignment(self, round_id: uuid.UUID) -> None:
        """Masukkan round ini ke antrean "perlu penugasan" Admin (AC8), tanpa commit."""
        ...


class Notifier(Protocol):
    """Notifikasi dalam aplikasi, sesuai notify(user_ids, type, payload) di SCRUM-153."""

    def notify(
        self, user_ids: Sequence[uuid.UUID], type: str, payload: Mapping[str, str]
    ) -> None: ...


class NoopNotifier:
    """Notifier kosong untuk tes atau pemanggil yang sengaja menonaktifkan kirim."""

    def notify(self, user_ids: Sequence[uuid.UUID], type: str, payload: Mapping[str, str]) -> None:
        return None
