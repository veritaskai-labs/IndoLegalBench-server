"""Kapan round diputuskan dan apa keputusannya (PBI-6 AC6, D2, D3 langkah 30).

Fungsi murni tanpa database, supaya aturannya bisa diuji sendiri dan
service cukup memanggilnya setelah verdict tersimpan.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from app.modules.reviews.models import AssignmentStatus, RoundDecision, VerdictDecision


def counted_decisions(assignments: Iterable[Any]) -> list[VerdictDecision]:
    """Keputusan yang dihitung: hanya verdict milik assignment yang masih aktif.

    Verdict dari assignment replaced atau void tidak dihitung (D4a, AC7).
    Approve yang dibawa ke round berikutnya (AC9) tersimpan sebagai verdict
    biasa di assignment aktif, jadi ikut dihitung.
    """
    return [
        tugas.verdict.decision
        for tugas in assignments
        if tugas.status == AssignmentStatus.ACTIVE and tugas.verdict is not None
    ]


def decide(decisions: Sequence[VerdictDecision], *, required: int) -> RoundDecision | None:
    """None sampai semua reviewer menilai. Satu revise cukup untuk revisi."""
    if len(decisions) < required:
        return None
    if VerdictDecision.REVISE in decisions:
        return RoundDecision.REVISE
    return RoundDecision.APPROVED
