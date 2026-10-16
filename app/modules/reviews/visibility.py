"""Siapa boleh melihat apa dari sebuah review (PBI-6 AC3, D3, D5a).

Aturan blind ditegakkan di sini, di server, bukan di tampilan: setiap
response yang memuat verdict menyaringnya lewat can_see_verdict.

Admin melihat semua verdict kapan pun. Ini keputusan klien di SCRUM-145
dan AC3, menggantikan asumsi #6 di D2 yang memberlakukan blind juga ke Admin.
"""

import uuid
from dataclasses import dataclass

from app.modules.reviews.models import RoundStatus
from app.shared.security import Role


@dataclass(frozen=True)
class Viewer:
    user_id: uuid.UUID
    role: Role


def can_see_verdict(viewer: Viewer, *, owner_id: uuid.UUID, round_status: RoundStatus) -> bool:
    """Verdict milik reviewer `owner_id` boleh ditampilkan ke viewer ini."""
    if viewer.role == Role.ADMIN:
        return True
    if round_status == RoundStatus.DECIDED:
        return True
    return viewer.role == Role.REVIEWER and viewer.user_id == owner_id


def can_open_case_review(viewer: Viewer, *, author_id: uuid.UUID, assigned: bool) -> bool:
    """Admin, penulis kasus, atau reviewer yang pernah ditugaskan di kasus ini."""
    if viewer.role == Role.ADMIN:
        return True
    if viewer.role == Role.AUTHOR:
        return viewer.user_id == author_id
    if viewer.role == Role.REVIEWER:
        return assigned
    return False
