"""Aturan murni sesi pengukuran, tanpa database. PBI-11 SCRUM-210.

Status percobaan ditulis sebagai string yang sama dengan enum attempts di D2
(pending, running, succeeded, technical_failure), supaya aturan ini bisa diuji
sebelum tabel attempts ada di staging.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.shared.exceptions import ConflictError

# Sesi yang sudah berakhir. Status PBI-12 (stopped, cancelled) belum ada di sini
# sampai D4 memutuskan apakah boleh diarsipkan.
_ENDED = frozenset({"finished", "finished_with_failures", "failed"})

# Percobaan yang sudah punya hasil akhir. Gagal teknis tetap selesai: coba ulang
# menambah call_logs, bukan attempts (D2).
_FINAL = frozenset({"succeeded", "technical_failure"})
_NOT_FINAL = frozenset({"pending", "running"})


@dataclass(frozen=True)
class SessionProgress:
    """Progres "X dari N" di daftar sesi (AC10)."""

    completed: int
    total: int


def progress(status_counts: Mapping[str, int]) -> SessionProgress:
    """X = percobaan selesai, N = semua percobaan sesi.

    Status di luar enum D2 ditolak supaya status baru (PBI-12) diputuskan
    dulu, bukan diam-diam terhitung belum selesai.
    """
    unknown = set(status_counts) - _FINAL - _NOT_FINAL
    if unknown:
        raise ValueError(f"Unknown attempt status: {', '.join(sorted(unknown))}")
    completed = sum(count for status, count in status_counts.items() if status in _FINAL)
    return SessionProgress(completed=completed, total=sum(status_counts.values()))


def has_ended(status: str) -> bool:
    """Status yang belum dikenal dianggap belum berakhir, supaya aturannya tetap ketat."""
    return status in _ENDED


def ensure_archivable(status: str, *, archived_at: datetime | None) -> None:
    """Sesi hanya boleh diarsipkan sekali, setelah berakhir (AC10).

    Sesi antre atau berjalan ditolak, jadi sesi yang diarsipkan tidak pernah
    memegang slot sesi otomatis sambil hilang dari daftar.
    """
    if archived_at is not None:
        raise ConflictError("Sesi ini sudah diarsipkan.", code="SESSION_ALREADY_ARCHIVED")
    if not has_ended(status):
        raise ConflictError(
            "Sesi hanya bisa diarsipkan setelah berakhir.",
            code="SESSION_NOT_ARCHIVABLE",
        )
