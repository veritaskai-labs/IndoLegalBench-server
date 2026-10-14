"""Logika bisnis modul reviews.

PBI-6 Review independen sebelum kasus disetujui. Ini satu-satunya pintu
masuk yang boleh dipanggil modul lain. Service tidak boleh menyentuh HTTP.

SCRUM-143 menyiapkan round untuk pengajuan kasus dan daftar kasus milik
reviewer. SCRUM-144 memilih dua reviewer. Fungsi yang dipanggil dari
dalam pengajuan kasus tidak pernah commit: cases.service memegang
transaksinya.
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.audit import service as audit_service
from app.modules.auth import service as auth_service
from app.modules.reviews import repository
from app.modules.reviews.models import ReviewRound, RoundStatus
from app.modules.reviews.ports import AssignmentStore, Notifier, ReviewerLoad

REVIEWERS_PER_ROUND = 2
REVIEW_ASSIGNED = "review_assigned"

_BELUM_PERNAH = ReviewerLoad(open_count=0, last_assigned_at=None, total_count=0)


def assign_on_submit(
    db: Session,
    *,
    round_id: uuid.UUID,
    case_id: uuid.UUID,
    author_id: uuid.UUID,
    store: AssignmentStore,
    notifier: Notifier,
) -> list[uuid.UUID]:
    """SCRUM-144: pilih dua reviewer aktif yang bukan penulis kasus (AC2).

    Dipanggil submit SCRUM-143 setelah round dibuat. Kurang dari dua
    reviewer yang memenuhi syarat berarti round masuk antrean "perlu
    penugasan" Admin (AC8), tanpa pasangan sebagian. Tidak pernah commit:
    submit memegang transaksinya. Pelaku di audit adalah pengguna yang
    terikat di session, yaitu Author yang mengajukan.
    """
    kandidat = [
        reviewer_id
        for reviewer_id in auth_service.active_reviewer_ids(db)
        if reviewer_id != author_id
    ]
    if len(kandidat) < REVIEWERS_PER_ROUND:
        store.mark_needs_assignment(round_id)
        return []

    beban = store.open_queue(kandidat)
    terpilih = sorted(
        kandidat,
        key=lambda reviewer_id: _urutan(reviewer_id, beban.get(reviewer_id, _BELUM_PERNAH)),
    )[:REVIEWERS_PER_ROUND]

    store.save_assignments(round_id, terpilih)
    notifier.notify(
        terpilih,
        REVIEW_ASSIGNED,
        {
            "title": "Tugas review baru",
            "body": "Anda ditugaskan mereview sebuah kasus.",
            # TODO(SCRUM-153): sesuaikan dengan rute halaman review di client.
            "link": f"/cases/{case_id}",
        },
    )
    audit_service.record(
        db,
        action="review.assigned",
        entity_type="review",
        entity_id=round_id,
        case_id=case_id,
        after={"reviewer_ids": [str(reviewer_id) for reviewer_id in terpilih]},
    )
    return terpilih


def _urutan(reviewer_id: uuid.UUID, beban: ReviewerLoad) -> tuple[int, bool, float, int, str]:
    """Antrean terkecil, belum pernah ditugaskan, penugasan terlama, total tersedikit, id.

    Dua reviewer satu round punya waktu penugasan yang sama. Tanpa total
    sebelum id, id terkecil selalu menang seri itu dan terus terpilih.
    """
    terakhir = beban.last_assigned_at
    return (
        beban.open_count,
        terakhir is not None,
        terakhir.timestamp() if terakhir else 0.0,
        beban.total_count,
        str(reviewer_id),
    )


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
