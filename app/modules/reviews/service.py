"""Logika bisnis modul reviews.

PBI-6. Ini satu-satunya pintu masuk yang boleh dipanggil modul lain.
SCRUM-143 menyiapkan round untuk pengajuan kasus dan daftar kasus milik
reviewer. SCRUM-144 memilih dua reviewer lewat assign_on_submit dan
menyimpannya di tabel review. Verdict dan penggantian reviewer menyusul
di SCRUM-145 sampai SCRUM-147. Notifikasi masih lewat port (SCRUM-153).

Service ini umumnya tidak commit (mis. pengajuan kasus mengubah status
versi dan membuat round dalam transaksi cases.service pemanggil), kecuali
fungsi yang dipanggil langsung oleh router seperti replace_reviewer yang
memiliki parameter auto_commit.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.cases import service as cases_service
from app.modules.reviews import repository
from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    RoundStatus,
)
from app.modules.reviews.ports import AssignmentStore, NoopNotifier, Notifier, ReviewerLoad
from app.modules.reviews.schemas import NeedsReassignmentResponse, ReassignmentAssignmentRead
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.security import Role

REVIEWERS_PER_ROUND = 2
REVIEW_ASSIGNED = "review_assigned"

_BELUM_PERNAH = ReviewerLoad(open_count=0, last_assigned_at=None, total_count=0)


def open_round(
    db: Session,
    *,
    case_id: uuid.UUID,
    case_version_id: uuid.UUID,
    author_id: uuid.UUID,
    store: AssignmentStore | None = None,
    notifier: Notifier | None = None,
) -> ReviewRound:
    """Buat round berikutnya untuk versi yang baru diajukan.

    Pengajuan pertama jadi round 1. Pengajuan ulang setelah revisi memakai
    versi yang sama, jadi nomornya naik (D3 langkah 44).

    Round dimulai awaiting_assignment. Untuk round 1, assign_on_submit
    memasang dua reviewer dan membuka round bila kandidat cukup. Round > 1
    tidak menugaskan ulang di sini (AC9 / SCRUM-146).
    """
    ronde = ReviewRound(
        case_id=case_id,
        case_version_id=case_version_id,
        round_no=repository.max_round_no(db, case_version_id) + 1,
        status=RoundStatus.AWAITING_ASSIGNMENT,
    )
    repository.add_round(db, ronde)
    if ronde.round_no == 1:
        assign_on_submit(
            db,
            round_id=ronde.id,
            case_id=case_id,
            author_id=author_id,
            store=store,
            notifier=notifier,
        )
    return ronde


def assigned_case_ids(db: Session, reviewer_id: uuid.UUID) -> frozenset[uuid.UUID]:
    """Kasus yang pernah ditugaskan ke reviewer ini (asumsi #10, D5b).

    Termasuk assignment yang sudah replaced atau void, supaya reviewer
    yang diganti Admin tetap bisa menelusuri jejak audit kasus yang pernah
    dia review. Dipakai audit.service untuk PBI-18 AC5 (SCRUM-167).
    """
    return frozenset(repository.case_ids_assigned_to(db, reviewer_id))


class ReviewTableStore:
    """AssignmentStore di atas tabel review SCRUM-143."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def open_queue(self, reviewer_ids):
        kelompok: dict[uuid.UUID, list[tuple[ReviewAssignment, bool]]] = {}
        for tugas, punya_verdict in repository.reviewer_assignments(self._db, reviewer_ids):
            kelompok.setdefault(tugas.reviewer_id, []).append((tugas, punya_verdict))
        hasil = {}
        for reviewer_id, milik in kelompok.items():
            terbuka = [
                tugas
                for tugas, punya_verdict in milik
                if tugas.status == AssignmentStatus.ACTIVE and not punya_verdict
            ]
            terakhir = max((tugas.assigned_at for tugas, _punya in milik), default=None)
            hasil[reviewer_id] = ReviewerLoad(
                open_count=len(terbuka),
                last_assigned_at=terakhir,
                total_count=len(milik),
            )
        return hasil

    def save_assignments(self, round_id, reviewer_ids) -> None:
        ronde = repository.get_round(self._db, round_id)
        for reviewer_id in reviewer_ids:
            tugas = ReviewAssignment(
                review_round_id=round_id,
                reviewer_id=reviewer_id,
                source=AssignmentSource.SYSTEM,
                status=AssignmentStatus.ACTIVE,
            )
            # Set relationship in memory so the D6a tracker can read case_id
            # during after_flush without a lazy load mid-flush.
            if ronde is not None:
                tugas.review_round = ronde
            repository.add_assignment(self._db, tugas)
        if ronde is not None:
            ronde.status = RoundStatus.OPEN
        self._db.flush()

    def mark_needs_assignment(self, round_id) -> None:
        ronde = repository.get_round(self._db, round_id)
        if ronde is not None:
            ronde.status = RoundStatus.AWAITING_ASSIGNMENT
        self._db.flush()


def assign_on_submit(
    db: Session,
    *,
    round_id: uuid.UUID,
    case_id: uuid.UUID,
    author_id: uuid.UUID,
    store: AssignmentStore | None = None,
    notifier: Notifier | None = None,
) -> list[uuid.UUID]:
    """SCRUM-144: pilih dua reviewer aktif yang bukan penulis kasus (AC2).

    Hanya untuk pengajuan pertama (round_no == 1). Pengajuan ulang setelah
    revisi memakai alur SCRUM-146 (AC9), bukan fungsi ini.

    Dipanggil open_round setelah round 1 dibuat. Tanpa store, penugasan
    ditulis ke tabel review. Kurang dari dua reviewer yang memenuhi syarat
    berarti round masuk antrean "perlu penugasan" Admin (AC8), tanpa
    pasangan sebagian. Tidak pernah commit: submit memegang transaksinya.

    Audit penugasan datang dari ReviewAssignmentTracker (D6a
    review.reviewer_assigned per baris), bukan dari record manual di sini.
    """
    penyimpanan = store if store is not None else ReviewTableStore(db)
    pemberitahu = notifier if notifier is not None else NoopNotifier()
    kandidat = [
        reviewer_id
        for reviewer_id in auth_service.active_reviewer_ids(db)
        if reviewer_id != author_id
    ]
    if len(kandidat) < REVIEWERS_PER_ROUND:
        penyimpanan.mark_needs_assignment(round_id)
        return []

    beban = penyimpanan.open_queue(kandidat)
    terpilih = sorted(
        kandidat,
        key=lambda reviewer_id: _urutan(reviewer_id, beban.get(reviewer_id, _BELUM_PERNAH)),
    )[:REVIEWERS_PER_ROUND]

    penyimpanan.save_assignments(round_id, terpilih)
    # Notifier runs inside the submit transaction today (NoopNotifier).
    # SCRUM-153 should keep notify non-blocking so a delivery failure does
    # not roll back the assignment rows already flushed in this txn.
    pemberitahu.notify(
        terpilih,
        REVIEW_ASSIGNED,
        {
            "title": "Tugas review baru",
            "body": "Anda ditugaskan mereview sebuah kasus.",
            # TODO(SCRUM-153): sesuaikan dengan rute halaman review di client.
            "link": f"/cases/{case_id}",
        },
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


def void_reviewer_assignments(
    db: Session,
    reviewer_id: uuid.UUID,
    *,
    reason: str = "Reviewer deactivated",
) -> list[ReviewAssignment]:
    """Void semua penugasan aktif milik reviewer yang dinonaktifkan (AC8, SCRUM-147).

    Dipanggil oleh AuthService.deactivate_member saat akun reviewer dinonaktifkan.
    Setiap assignment diubah statusnya jadi void, ended_at diisi, dan bila round-nya
    belum diputuskan (decided), status round dikembalikan ke awaiting_assignment.
    """
    assignments = repository.get_active_assignments_by_reviewer(db, reviewer_id)
    for assignment in assignments:
        assignment.status = AssignmentStatus.VOID
        assignment.ended_at = datetime.now(UTC)
        assignment.end_reason = reason
        ronde = assignment.review_round or repository.get_round(db, assignment.review_round_id)
        if ronde is not None:
            assignment.review_round = ronde
            if ronde.status != RoundStatus.DECIDED and ronde.decision is None:
                ronde.status = RoundStatus.AWAITING_ASSIGNMENT
    if assignments:
        db.flush()
    return assignments


def replace_reviewer(
    db: Session,
    *,
    assignment_id: uuid.UUID,
    new_reviewer_id: uuid.UUID,
    reason: str,
    admin_id: uuid.UUID,
    author_id: uuid.UUID | None = None,
    notifier: Notifier | None = None,
    auto_commit: bool = True,
) -> tuple[ReviewAssignment, ReviewAssignment]:
    """Ganti reviewer pada assignment_id dengan new_reviewer_id (AC7, SCRUM-147).

    - Assignment lama status diubah jadi replaced, ended_at diisi, end_reason dicatat.
    - Assignment baru dibuat dengan source = ADMIN, status = ACTIVE.
    - Notifikasi dikirimkan ke reviewer baru via notifier.
    - Reviewer baru tidak boleh:
      1. Penulis kasus (422 CANNOT_ASSIGN_AUTHOR)
      2. Reviewer yang sedang diganti atau reviewer lain di round itu (422 REVIEWER_ALREADY_ASSIGNED)
      3. Bukan akun reviewer yang aktif (422 INVALID_REVIEWER)
    - Jejak audit dicatat otomatis (review.reviewer_replaced dengan reason dan review.reviewer_assigned).
    """
    clean_reason = reason.strip() if reason else ""
    if not clean_reason:
        raise ValidationError("Alasan wajib diisi untuk aksi ini", field="reason")

    old_assignment = repository.get_assignment(db, assignment_id)
    if not old_assignment:
        raise NotFoundError("Penugasan review tidak ditemukan")

    if old_assignment.status == AssignmentStatus.REPLACED:
        raise ConflictError(
            "Penugasan ini sudah pernah diganti",
            code="ASSIGNMENT_ALREADY_REPLACED",
        )

    ronde = old_assignment.review_round or repository.get_round(db, old_assignment.review_round_id)
    if not ronde:
        raise NotFoundError("Putaran review tidak ditemukan")

    if ronde.status == RoundStatus.DECIDED or ronde.decision is not None:
        raise ConflictError(
            "Putaran review sudah selesai diputuskan",
            code="ROUND_ALREADY_DECIDED",
        )

    # Validasi reviewer baru
    new_user = auth_service.get_user(db, new_reviewer_id)
    if not new_user:
        raise NotFoundError("Reviewer baru tidak ditemukan")

    # Validasi penulis kasus (AC7: Reviewer baru tidak boleh penulis)
    case_author_id = author_id
    if case_author_id is None:
        case_info = cases_service.get_case_for_review(db, ronde.case_id)
        if case_info is not None:
            _, _, case_author_id = case_info
    if case_author_id is not None and new_reviewer_id == case_author_id:
        raise ValidationError(
            "Reviewer baru tidak boleh penulis kasus",
            code="CANNOT_ASSIGN_AUTHOR",
            field="new_reviewer_id",
        )

    if not new_user.is_active or new_user.role != Role.REVIEWER:
        raise ValidationError(
            "Reviewer baru harus berupa akun reviewer yang aktif",
            code="INVALID_REVIEWER",
            field="new_reviewer_id",
        )

    # Validasi reviewer lain di round itu (AC7: tidak boleh reviewer lain di round itu)
    active_assignments = repository.get_active_assignments_for_round(db, ronde.id)
    other_active_reviewer_ids = {
        a.reviewer_id for a in active_assignments if a.id != old_assignment.id
    }
    if new_reviewer_id in other_active_reviewer_ids:
        raise ValidationError(
            "Reviewer baru tidak boleh reviewer lain di round itu",
            code="REVIEWER_ALREADY_ASSIGNED",
            field="new_reviewer_id",
        )
    if new_reviewer_id == old_assignment.reviewer_id:
        raise ValidationError(
            "Reviewer baru tidak boleh reviewer yang sedang diganti",
            code="REVIEWER_ALREADY_ASSIGNED",
            field="new_reviewer_id",
        )

    # Update assignment lama
    old_assignment.status = AssignmentStatus.REPLACED
    old_assignment.ended_at = datetime.now(UTC)
    old_assignment.end_reason = clean_reason
    old_assignment.review_round = ronde

    # Buat assignment baru
    new_assignment = ReviewAssignment(
        review_round_id=ronde.id,
        reviewer_id=new_reviewer_id,
        source=AssignmentSource.ADMIN,
        assigned_by=admin_id,
        status=AssignmentStatus.ACTIVE,
    )
    new_assignment.review_round = ronde
    repository.add_assignment(db, new_assignment)

    # Update status round: bila active reviewer sudah mencapai REVIEWERS_PER_ROUND -> OPEN
    remaining_active = [a for a in active_assignments if a.id != old_assignment.id]
    total_active_count = len(remaining_active) + 1
    if total_active_count >= REVIEWERS_PER_ROUND:
        ronde.status = RoundStatus.OPEN
    else:
        ronde.status = RoundStatus.AWAITING_ASSIGNMENT

    # Notifikasi reviewer baru
    pemberitahu = notifier if notifier is not None else NoopNotifier()
    pemberitahu.notify(
        [new_reviewer_id],
        REVIEW_ASSIGNED,
        {
            "title": "Tugas review baru",
            "body": "Anda ditugaskan mereview sebuah kasus.",
            "link": f"/cases/{ronde.case_id}",
        },
    )

    db.flush()
    if auto_commit:
        db.commit()

    return old_assignment, new_assignment


def get_needs_reassignment(db: Session) -> list[NeedsReassignmentResponse]:
    """Daftar kasus yang memerlukan penugasan reviewer ulang (AC8, SCRUM-147)."""
    rounds = repository.get_rounds_needing_reassignment(db)
    if not rounds:
        return []

    case_ids = [r.case_id for r in rounds]
    case_map = cases_service.get_cases_for_review(db, case_ids)

    result: list[NeedsReassignmentResponse] = []
    for ronde in rounds:
        assignments = repository.get_assignments_for_round(db, ronde.id)
        reviewer_ids = {a.reviewer_id for a in assignments}
        name_map = auth_service.user_names(db, reviewer_ids)

        assignments_read = [
            ReassignmentAssignmentRead(
                id=a.id,
                reviewer_id=a.reviewer_id,
                reviewer_name=name_map.get(a.reviewer_id),
                status=a.status,
                source=a.source,
                assigned_at=a.assigned_at,
                ended_at=a.ended_at,
                end_reason=a.end_reason,
            )
            for a in assignments
        ]
        void_assignments = [a for a in assignments_read if a.status == AssignmentStatus.VOID]
        active_count = len([a for a in assignments if a.status == AssignmentStatus.ACTIVE])

        case_info = case_map.get(ronde.case_id)
        if case_info is not None:
            case_code, case_title, _ = case_info
        else:
            case_code = str(ronde.case_id)[:8]
            case_title = ""

        result.append(
            NeedsReassignmentResponse(
                case_id=ronde.case_id,
                case_code=case_code,
                case_title=case_title,
                round_id=ronde.id,
                round_no=ronde.round_no,
                round_status=ronde.status,
                active_reviewers_count=active_count,
                void_assignments=void_assignments,
                assignments=assignments_read,
            )
        )
    return result
