"""Logika bisnis modul reviews.

PBI-6. Ini satu-satunya pintu masuk yang boleh dipanggil modul lain.
SCRUM-143 menyiapkan round untuk pengajuan kasus dan daftar kasus milik
reviewer. Penugasan, verdict, dan penggantian reviewer menyusul di
SCRUM-144 sampai SCRUM-147.

Service ini tidak pernah commit. Pengajuan kasus mengubah status versi
(milik cases) dan membuat round (milik reviews) dalam satu transaksi,
jadi commit dilakukan cases.service sebagai pemanggil.
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.reviews import repository
from app.modules.reviews.models import ReviewRound, RoundStatus


def open_round(db: Session, *, case_id: uuid.UUID, case_version_id: uuid.UUID) -> ReviewRound:
    """Buat round berikutnya untuk versi yang baru diajukan.

    Pengajuan pertama jadi round 1. Pengajuan ulang setelah revisi memakai
    versi yang sama, jadi nomornya naik (D3 langkah 44).

    TODO(SCRUM-144): pasang dua reviewer di sini. Sampai itu ada, round
    tetap awaiting_assignment, sama seperti saat kandidat kurang dari dua.
    """
    ronde = ReviewRound(
        case_id=case_id,
        case_version_id=case_version_id,
        round_no=repository.max_round_no(db, case_version_id) + 1,
        status=RoundStatus.AWAITING_ASSIGNMENT,
    )
    repository.add_round(db, ronde)
    return ronde


def assigned_case_ids(db: Session, reviewer_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Kasus yang pernah ditugaskan ke reviewer ini (asumsi #10, D5b).

    Termasuk assignment yang sudah replaced atau void, supaya reviewer
    yang diganti Admin tetap bisa menelusuri jejak audit kasus yang pernah
    dia review. Dipakai audit.service untuk PBI-18 AC5 (SCRUM-167).
    """
    return frozenset(repository.case_ids_assigned_to(db, reviewer_id))
