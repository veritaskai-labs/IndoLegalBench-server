"""Aturan perpindahan status sesi pengukuran per D4. PBI-12 AC7, AC9, SCRUM-214.

Satu tabel menjadi sumber kebenaran: setiap status memetakan ke himpunan status
tujuan yang sah. Pasangan di luar tabel ditolak dengan 409 INVALID_TRANSITION.
Penjaga tambahan (versi model dan batas hari saat Lanjutkan) ada di SCRUM-216.
"""

from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError

_S = SessionStatus

ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    # Giliran antrean, atau dibatalkan sebelum ada hasil ([USULAN SA]).
    _S.QUEUED: frozenset({_S.RUNNING, _S.CANCELLED}),
    _S.RUNNING: frozenset(
        {_S.FINISHED, _S.FINISHED_WITH_FAILURES, _S.FAILED, _S.PAUSED, _S.CANCELLED}
    ),
    # Lanjutkan (AC8).
    _S.PAUSED: frozenset({_S.RUNNING}),
    _S.CANCELLED: frozenset({_S.RUNNING}),
    # Coba ulang yang gagal (AC3).
    _S.FINISHED_WITH_FAILURES: frozenset({_S.RUNNING}),
    _S.FINISHED: frozenset(),
    _S.FAILED: frozenset(),
}


def ensure_transition(current: SessionStatus | str, target: SessionStatus | str) -> None:
    """Tolak perpindahan yang tidak digambar di D4. Menerima string dari database."""
    current, target = SessionStatus(current), SessionStatus(target)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ConflictError(
            f"Status sesi tidak bisa berpindah dari {current.value} ke {target.value}.",
            code="INVALID_TRANSITION",
            details={"from": current.value, "to": target.value},
        )
