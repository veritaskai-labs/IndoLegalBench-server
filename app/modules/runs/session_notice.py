"""Notifikasi saat sesi pengukuran berakhir. PBI-11 AC10, SCRUM-210, D2 Tabel 7.

Hanya di dalam aplikasi, ke satu penerima: pemulai sesi. Tidak ada notifikasi
anggaran, peringatan dini, atau ambang kegagalan.

Pemanggilnya: SessionQueue saat sesi gagal sebelum mulai (SCRUM-207). Akhir sesi
yang sudah berjalan menyusul bersama worker SCRUM-208. Fungsi ini tidak melempar
untuk status yang tidak dikenal, supaya akhir sesi tidak gagal karena notifikasi.
"""

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from app.modules.notifications.types import MEASUREMENT_COMPLETED, MEASUREMENT_FAILED

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    """Sama dengan notify(user_ids, type, payload) milik DbNotifier (SCRUM-153)."""

    def notify(
        self, user_ids: Sequence[uuid.UUID], type: str, payload: Mapping[str, str]
    ) -> None: ...


# Status akhir -> (tipe, judul, isi). finished_with_failures dianggap selesai
# sampai D4 / SCRUM-240 memutuskan lain.
_PESAN: Mapping[str, tuple[str, str, Callable[[str], str]]] = {
    "finished": (
        MEASUREMENT_COMPLETED,
        "Pengukuran selesai",
        lambda label: f'Sesi "{label}" selesai.',
    ),
    "finished_with_failures": (
        MEASUREMENT_COMPLETED,
        "Pengukuran selesai",
        lambda label: f'Sesi "{label}" selesai, tetapi ada percobaan yang gagal teknis.',
    ),
    "failed": (
        MEASUREMENT_FAILED,
        "Pengukuran gagal",
        lambda label: f'Sesi "{label}" gagal.',
    ),
}

_BELUM_BERAKHIR = frozenset({"queued", "running"})


def notify_session_ended(
    notifier: Notifier,
    *,
    session_id: uuid.UUID,
    label: str,
    status: str,
    started_by: uuid.UUID,
) -> None:
    """Kirim satu notifikasi ke pemulai sesi bila status sesi sudah akhir."""
    pesan = _PESAN.get(status)
    if pesan is None:
        if status not in _BELUM_BERAKHIR:
            logger.warning("No session-end notification for status %s", status)
        return
    tipe, judul, isi = pesan
    notifier.notify(
        [started_by],
        tipe,
        {"title": judul, "body": isi(label), "link": f"/runs/{session_id}"},
    )
