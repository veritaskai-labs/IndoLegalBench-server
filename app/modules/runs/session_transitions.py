"""Aturan perpindahan status sesi pengukuran per D4 dan D8. PBI-12 SCRUM-214, PBI-22 SCRUM-230.

Satu tabel menjadi sumber kebenaran: setiap status memetakan ke himpunan status
tujuan yang sah. Pasangan di luar tabel ditolak dengan 409 INVALID_TRANSITION.
Penjaga tambahan (versi model dan batas hari saat Lanjutkan) ada di SCRUM-216.
"""

from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError

_S = SessionStatus

ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    # Giliran antrean, dibatalkan sebelum ada hasil ([USULAN SA]), atau gagal disiapkan
    # (SessionQueue SCRUM-207; [USULAN SA] "failed = kesalahan fatal sejak awal").
    _S.QUEUED: frozenset({_S.RUNNING, _S.CANCELLED, _S.FAILED}),
    _S.RUNNING: frozenset(
        {_S.FINISHED, _S.FINISHED_WITH_FAILURES, _S.FAILED, _S.PAUSED, _S.CANCELLED}
    ),
    # Lanjutkan (AC8).
    # SCRUM-216: SA approved pause→cancel and never-started cancellation requeue:
    # https://github.com/veritaskai-labs/IndoLegalBench-server/pull/102#pullrequestreview-5783940566
    # SCRUM-267: Lanjutkan saat slot terpakai atau ada sesi Menunggu kembali ke antrean (AC8).
    _S.PAUSED: frozenset({_S.RUNNING, _S.CANCELLED, _S.QUEUED}),
    _S.CANCELLED: frozenset({_S.RUNNING, _S.QUEUED}),
    # Coba ulang yang gagal (AC3).
    _S.FINISHED_WITH_FAILURES: frozenset({_S.RUNNING}),
    _S.FINISHED: frozenset(),
    _S.FAILED: frozenset(),
    # D8, sesi manual. Status pengisian dihitung ulang setiap kali jawaban disimpan;
    # jawaban pertama bisa langsung melengkapi sesi 1 kasus x 1 percobaan.
    _S.NOT_FILLED: frozenset({_S.PARTIALLY_FILLED, _S.COMPLETE}),
    _S.PARTIALLY_FILLED: frozenset({_S.COMPLETE}),
    # Selesai dari Lengkap atau Dinilai: urutan Selesai dan Nilai masih TBA (keputusan
    # SA 7 November 2026). Selesai tidak bisa dibuka kembali (PBI-22 AC8).
    _S.COMPLETE: frozenset({_S.GRADED, _S.FINALIZED}),
    _S.GRADED: frozenset({_S.FINALIZED}),
    _S.FINALIZED: frozenset(),
}


def sources_of(target: SessionStatus | str) -> frozenset[SessionStatus]:
    """Status asal yang boleh berpindah ke target; dipakai UPDATE bersyarat."""
    target = SessionStatus(target)
    return frozenset(asal for asal, tujuan in ALLOWED_TRANSITIONS.items() if target in tujuan)


def invalid_transition(current: SessionStatus | str, target: SessionStatus | str) -> ConflictError:
    current, target = SessionStatus(current), SessionStatus(target)
    return ConflictError(
        f"Status sesi tidak bisa berpindah dari {current.value} ke {target.value}.",
        code="INVALID_TRANSITION",
        details={"from": current.value, "to": target.value},
    )


def ensure_transition(current: SessionStatus | str, target: SessionStatus | str) -> None:
    """Tolak perpindahan yang tidak digambar di D4. Menerima string dari database."""
    if SessionStatus(target) not in ALLOWED_TRANSITIONS[SessionStatus(current)]:
        raise invalid_transition(current, target)
