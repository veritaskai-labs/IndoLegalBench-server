"""Aturan murni sesi manual per D8. PBI-22 (SCRUM-176) AC5, AC8, AC13; SCRUM-230.

Status pengisian dihitung ulang server setiap kali jawaban disimpan (D8): Belum terisi,
Terisi sebagian, atau Lengkap. Dinilai dan Selesai ditulis oleh aksi Admin, bukan dari
jumlah jawaban, jadi tidak ikut dihitung ulang. Perpindahan yang sah tetap satu tabel,
session_transitions.ALLOWED_TRANSITIONS.

Jawaban terisi = teks jawaban tidak kosong atau opsi "Platform menolak menjawab"
(D8 "Aturan pengisian"); penghitungnya ada di SCRUM-231 saat jawaban disimpan.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError

_S = SessionStatus

MANUAL_STATUSES = frozenset(
    {_S.NOT_FILLED, _S.PARTIALLY_FILLED, _S.COMPLETE, _S.GRADED, _S.FINALIZED}
)
# Dihitung dari jumlah jawaban; Dinilai dan Selesai tidak.
_FILLING = frozenset({_S.NOT_FILLED, _S.PARTIALLY_FILLED, _S.COMPLETE})
_STILL_FILLING = frozenset({_S.NOT_FILLED, _S.PARTIALLY_FILLED, _S.COMPLETE, _S.GRADED})
# Urutan Selesai dan Nilai masih TBA, jadi keduanya boleh (keputusan SA 7 November 2026).
_FINALIZABLE = frozenset({_S.COMPLETE, _S.GRADED})

_FINALIZED_MESSAGE = "Sesi manual sudah ditandai selesai dan tidak dapat dibuka kembali."


@dataclass(frozen=True)
class ManualProgress:
    """Progres "X dari N jawaban terisi" (AC8). N = kasus snapshot x jumlah percobaan."""

    filled: int
    total: int

    def __post_init__(self) -> None:
        # Sesi manual selalu punya minimal satu kasus dan satu percobaan (AC1).
        if self.total < 1 or not 0 <= self.filled <= self.total:
            raise ValueError(f"Impossible manual progress {self.filled} of {self.total}")


def filling_status(current: SessionStatus | str, progress: ManualProgress) -> SessionStatus:
    """Status sesi manual setelah jawaban disimpan. Dinilai dan Selesai tetap."""
    current = SessionStatus(current)
    if current in (_S.GRADED, _S.FINALIZED):
        return current
    if current not in _FILLING:
        raise ValueError(f"{current.value} is not a manual session status")
    if progress.filled == 0:
        return _S.NOT_FILLED
    if progress.filled == progress.total:
        return _S.COMPLETE
    return _S.PARTIALLY_FILLED


def ensure_finalizable(status: SessionStatus | str) -> None:
    """Selesai hanya bila semua kasus dan percobaan terisi, dan hanya sekali (AC8)."""
    status = SessionStatus(status)
    if status is _S.FINALIZED:
        raise ConflictError(_FINALIZED_MESSAGE, code="MANUAL_SESSION_FINALIZED")
    if status not in _FINALIZABLE:
        raise ConflictError(
            "Sesi hanya dapat ditandai selesai bila semua kasus dan percobaan sudah terisi.",
            code="MANUAL_SESSION_INCOMPLETE",
        )


def ensure_answers_open(status: SessionStatus | str) -> None:
    """Jawaban boleh diisi atau ditimpa sampai sesi Selesai (AC8). Dipakai SCRUM-231."""
    status = SessionStatus(status)
    if status is _S.FINALIZED:
        raise ConflictError(_FINALIZED_MESSAGE, code="MANUAL_SESSION_FINALIZED")
    if status not in _STILL_FILLING:
        raise ValueError(f"{status.value} is not a manual session status")


def answer_date_range(
    answered_at: Iterable[datetime | None],
) -> tuple[datetime | None, datetime | None]:
    """Tanggal jawaban paling awal dan paling akhir dari platform (AC13, D9)."""
    dates = [value for value in answered_at if value is not None]
    if not dates:
        return None, None
    return min(dates), max(dates)


def date_range_exceeds(
    start: datetime | None, end: datetime | None, *, limit_days: int | None
) -> bool:
    """Peringatan rentang tanggal (AC13). Batas hari TBA dari klien: None = belum aktif."""
    if limit_days is None or start is None or end is None:
        return False
    return end - start > timedelta(days=limit_days)
