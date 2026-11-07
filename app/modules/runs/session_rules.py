"""Aturan murni sesi pengukuran, tanpa database. PBI-11 SCRUM-210.

Status percobaan ditulis sebagai string yang sama dengan enum attempts di D2
(pending, running, succeeded, technical_failure), supaya aturan ini bisa diuji
sebelum tabel attempts ada di staging.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError, ForbiddenError
from app.shared.security import Role

NOT_YOUR_SESSION = "NOT_YOUR_SESSION"

# Sesi yang sudah berakhir. Status PBI-12 (stopped, cancelled) belum ada di sini
# sampai D4 memutuskan apakah boleh diarsipkan. Sesi manual berakhir saat Selesai (D8).
_ENDED = frozenset({"finished", "finished_with_failures", "failed", "finalized"})

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


def is_report_eligible(status: SessionStatus | str, *, accepted_as_is_at: datetime | None) -> bool:
    """Sesi yang boleh jadi dasar laporan (D4, PBI-12 AC7 dan AC9; dipakai PBI-15/16).

    finished layak. finished_with_failures layak hanya bila Admin menerimanya apa
    adanya. cancelled tidak layak sampai dilanjutkan dan selesai; status lain belum
    berakhir atau gagal. Sesi manual layak setelah Selesai (D8: "layak jadi dasar
    laporan"). Status di luar enum ditolak (ValueError), bukan dianggap tidak layak.
    """
    status = SessionStatus(status)
    if status in (SessionStatus.FINISHED, SessionStatus.FINALIZED):
        return True
    return status is SessionStatus.FINISHED_WITH_FAILURES and accepted_as_is_at is not None


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


def ensure_unarchivable(*, archived_at: datetime | None) -> None:
    """Sesi hanya bisa dikembalikan dari arsip bila memang sedang diarsipkan (AC10).

    Status tidak dicek: arsip hanya menerima sesi yang sudah berakhir, dan
    mengembalikannya tidak mengubah status.
    """
    if archived_at is None:
        raise ConflictError("Sesi ini belum diarsipkan.", code="SESSION_NOT_ARCHIVED")


def ensure_owner_or_admin(
    *, actor_id: uuid.UUID, actor_role: Role, created_by: uuid.UUID, action: str
) -> None:
    """Admin mengelola sesi siapa pun, Developer AI hanya sesi yang ia buat (D10).

    action melengkapi pesan, mis. "mengarsipkan". Kode galatnya selalu NOT_YOUR_SESSION.
    """
    if actor_role == Role.DEVELOPER_AI and created_by != actor_id:
        raise ForbiddenError(
            f"Developer AI hanya bisa {action} sesi yang ia buat.", code=NOT_YOUR_SESSION
        )
