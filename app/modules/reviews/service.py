"""Logika bisnis modul reviews.

PBI-6. Ini satu-satunya pintu masuk yang boleh dipanggil modul lain.
SCRUM-143 menyiapkan round untuk pengajuan kasus dan daftar kasus milik
reviewer. SCRUM-144 memilih dua reviewer lewat assign_on_submit dan
menyimpannya di tabel review. Verdict dan penggantian reviewer menyusul
di SCRUM-145 sampai SCRUM-147. Notifikasi masih lewat port (SCRUM-153).

Service ini umumnya tidak commit (mis. pengajuan kasus mengubah status
versi dan membuat round dalam transaksi cases.service pemanggil), kecuali
fungsi tingkat atas seperti submit_verdict dan replace_reviewer.

SCRUM-145 menerima verdict dan memutuskan round. submit_verdict adalah
aksi tingkat atas, jadi ia yang commit. Aturan keputusan ada di
decision.py, aturan blind di visibility.py.
"""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.notifications.service import DbNotifier
from app.modules.reviews import repository
from app.modules.reviews.decision import counted_decisions, decide
from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    ReviewVerdict,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)
from app.modules.reviews.ports import AssignmentStore, NoopNotifier, Notifier, ReviewerLoad
from app.modules.reviews.schemas import (
    AssignedReview,
    CaseReview,
    NeedsReassignmentResponse,
    PendingReview,
    ReassignmentAssignmentRead,
    ReviewerRead,
    ReviewerSlot,
    VerdictResult,
    VerdictView,
)
from app.modules.reviews.visibility import Viewer, can_open_case_review, can_see_verdict
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.shared.security import Role

REVIEWERS_PER_ROUND = 2
REVIEW_ASSIGNED = "review_assigned"
REVIEW_DECIDED = "review_decided"

COMMENT_REQUIRED = "COMMENT_REQUIRED"
NOT_YOUR_ASSIGNMENT = "NOT_YOUR_ASSIGNMENT"
REVIEWER_IS_AUTHOR = "REVIEWER_IS_AUTHOR"
ASSIGNMENT_NOT_ACTIVE = "ASSIGNMENT_NOT_ACTIVE"
ROUND_NOT_OPEN = "ROUND_NOT_OPEN"
ROUND_NOT_REVISED = "ROUND_NOT_REVISED"
VERDICT_ALREADY_SUBMITTED = "VERDICT_ALREADY_SUBMITTED"
NO_REVIEW_ROUND = "NO_REVIEW_ROUND"
ASSIGNMENT_NOT_FOUND = "Penugasan review tidak ditemukan"

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

    Pengajuan pertama jadi round 1. Nomor dihitung pada versi yang sama,
    jadi pemanggilan berikutnya menaikkan nomornya. Pengajuan ulang setelah
    revisi tidak lewat sini; itu resume_after_revision.

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


def hidden_verdict_assignments(
    db: Session, assignment_ids: Collection[uuid.UUID], *, viewer_id: uuid.UUID, viewer_role: Role
) -> frozenset[uuid.UUID]:
    """Assignment yang verdict-nya belum boleh dilihat viewer (PBI-6 AC3)."""
    if not assignment_ids:
        return frozenset()
    viewer = Viewer(viewer_id, viewer_role)
    return frozenset(
        assignment_id
        for assignment_id, owner_id, round_status in repository.verdict_owners(db, assignment_ids)
        if not can_see_verdict(viewer, owner_id=owner_id, round_status=round_status)
    )


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
            # case_id audit uses load_on_pending on review_round; no hand-set.
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
    pemberitahu = notifier if notifier is not None else DbNotifier()
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
    # DbNotifier memakai sesi sendiri; kegagalan kirim tidak mengotori
    # transaksi submit dan tidak melempar ke pemanggil.
    pemberitahu.notify(terpilih, REVIEW_ASSIGNED, _payload_tugas(case_id))
    return terpilih


def resume_after_revision(
    db: Session,
    *,
    case_id: uuid.UUID,
    case_version_id: uuid.UUID,
    notifier: Notifier | None = None,
) -> ReviewRound:
    """SCRUM-146: buka round berikutnya pada versi yang sama setelah revisi.

    Approval yang assignment-nya masih active disalin, dengan
    carried_from_verdict_id menunjuk verdict lama. Hanya reviewer yang
    memilih revise mendapat assignment baru tanpa verdict, dan hanya mereka
    yang diberi tahu. Verdict lama tidak diubah, jadi komentar revisi tetap
    terbaca. Tidak memilih reviewer baru. Round terakhir harus sudah
    decided dengan keputusan revise, kalau tidak pengajuan ditolak.
    Perevisi yang sudah bukan reviewer aktif tidak diganti; round-nya
    menunggu penugasan Admin.
    """
    pemberitahu = notifier if notifier is not None else DbNotifier()
    sebelumnya = repository.latest_round(db, case_version_id)
    if (
        sebelumnya is None
        or sebelumnya.status != RoundStatus.DECIDED
        or sebelumnya.decision != RoundDecision.REVISE
    ):
        raise ConflictError(
            "Pengajuan ulang hanya bisa dilakukan setelah putaran review memutuskan revisi.",
            code=ROUND_NOT_REVISED,
        )
    ronde = ReviewRound(
        case_id=case_id,
        case_version_id=case_version_id,
        round_no=repository.max_round_no(db, case_version_id) + 1,
        status=RoundStatus.OPEN,
    )
    repository.add_round(db, ronde)
    dibawa = 0
    revisi: list[uuid.UUID] = []
    masih_aktif = set(auth_service.active_reviewer_ids(db))
    for tugas in repository.assignments_in_round(db, sebelumnya.id):
        if tugas.status != AssignmentStatus.ACTIVE:
            continue
        verdict = tugas.verdict
        if verdict is not None and verdict.decision == VerdictDecision.APPROVE:
            _bawa_approve(db, ronde, tugas.reviewer_id, verdict)
            dibawa += 1
        elif (
            verdict is not None
            and verdict.decision == VerdictDecision.REVISE
            and tugas.reviewer_id in masih_aktif
        ):
            _tugaskan(db, ronde, tugas.reviewer_id)
            revisi.append(tugas.reviewer_id)
    if dibawa + len(revisi) < REVIEWERS_PER_ROUND:
        ronde.status = RoundStatus.AWAITING_ASSIGNMENT
    db.flush()
    if revisi:
        pemberitahu.notify(revisi, REVIEW_ASSIGNED, _payload_tugas(case_id))
    return ronde


def _bawa_approve(
    db: Session, ronde: ReviewRound, reviewer_id: uuid.UUID, verdict: ReviewVerdict
) -> None:
    """Salin approve ke round baru. Baris verdict lama dibiarkan."""
    tugas = _tugaskan(db, ronde, reviewer_id)
    db.flush()
    repository.add_verdict(
        db,
        ReviewVerdict(
            assignment_id=tugas.id,
            decision=VerdictDecision.APPROVE,
            comment=verdict.comment,
            carried_from_verdict_id=verdict.id,
        ),
    )


def _tugaskan(db: Session, ronde: ReviewRound, reviewer_id: uuid.UUID) -> ReviewAssignment:
    """Buat assignment aktif. case_id audit lewat load_on_pending pada review_round."""
    tugas = ReviewAssignment(
        review_round_id=ronde.id,
        reviewer_id=reviewer_id,
        source=AssignmentSource.SYSTEM,
        status=AssignmentStatus.ACTIVE,
    )
    repository.add_assignment(db, tugas)
    return tugas


def _payload_tugas(case_id: uuid.UUID) -> dict[str, str]:
    return {
        "title": "Tugas review baru",
        "body": "Anda ditugaskan mereview sebuah kasus.",
        # TODO(SCRUM-153): sesuaikan dengan rute halaman review di client.
        "link": f"/cases/{case_id}",
    }


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


def submit_verdict(
    db: Session,
    assignment_id: uuid.UUID,
    *,
    reviewer_id: uuid.UUID,
    decision: VerdictDecision,
    comment: str | None,
    notifier: Notifier | None = None,
) -> VerdictResult:
    """Simpan verdict reviewer, lalu putuskan round bila semua sudah menilai.

    PBI-6 AC2-AC6, D3 langkah 21-41. Round dikunci sebelum menghitung,
    jadi dua verdict yang masuk hampir bersamaan tetap diputuskan tepat
    sekali. Penulis diberi tahu setelah COMMIT (D3 langkah 34/39).
    """
    komentar = (comment or "").strip() or None
    if decision == VerdictDecision.REVISE and komentar is None:
        raise ValidationError(
            "Komentar wajib diisi bila meminta revisi", code=COMMENT_REQUIRED, field="comment"
        )
    tugas = repository.get_assignment(db, assignment_id)
    if tugas is None:
        raise NotFoundError(ASSIGNMENT_NOT_FOUND)
    if tugas.reviewer_id != reviewer_id:
        raise ForbiddenError("Penugasan ini milik reviewer lain", code=NOT_YOUR_ASSIGNMENT)
    salinan = _cases().review_copies(db, {tugas.review_round.case_version_id})[
        tugas.review_round.case_version_id
    ]
    if salinan.author_id == reviewer_id:
        raise ForbiddenError(
            "Penulis kasus tidak boleh menilai kasusnya sendiri", code=REVIEWER_IS_AUTHOR
        )

    ronde = repository.lock_round(db, tugas.review_round_id)
    # Baca ulang sesudah kunci: assignment di atas dimuat sebelum menunggu
    # kunci, jadi statusnya bisa sudah basi bila reviewer baru saja diganti.
    tugas = repository.get_assignment(db, assignment_id, fresh=True)
    _require_can_submit(tugas, ronde)
    verdict = ReviewVerdict(assignment_id=tugas.id, decision=decision, comment=komentar)
    try:
        repository.add_verdict(db, verdict)
    except IntegrityError:
        db.rollback()
        raise _already_submitted() from None

    tugas_round = repository.assignments_in_round(db, ronde.id)
    keputusan = decide(counted_decisions(tugas_round), required=REVIEWERS_PER_ROUND)
    if keputusan is not None:
        _close_round(db, ronde, keputusan)
    db.commit()

    if keputusan is not None:
        _notify_author(
            notifier if notifier is not None else DbNotifier(), salinan, keputusan, tugas_round
        )
    return VerdictResult(
        assignment_id=tugas.id,
        decision=verdict.decision,
        comment=verdict.comment,
        submitted_at=verdict.submitted_at,
        round_status=ronde.status,
        round_decision=ronde.decision,
    )


def _require_can_submit(tugas: ReviewAssignment, ronde: ReviewRound) -> None:
    """Dicek pada assignment yang dibaca ulang setelah round dikunci.

    Penggantian reviewer (AC7, SCRUM-147) wajib mengunci round yang sama
    sebelum mengubah status assignment. Dengan begitu penggantian yang
    selesai lebih dulu selalu terlihat di sini, dan yang datang sesudahnya
    menunggu verdict ini selesai.
    """
    if tugas.status != AssignmentStatus.ACTIVE:
        raise ConflictError(
            "Penugasan ini sudah diganti atau dibatalkan", code=ASSIGNMENT_NOT_ACTIVE
        )
    if ronde.status != RoundStatus.OPEN:
        raise ConflictError("Round review ini sedang tidak menerima penilaian", code=ROUND_NOT_OPEN)
    if tugas.verdict is not None:
        raise _already_submitted()


def _already_submitted() -> ConflictError:
    return ConflictError(
        "Penilaian untuk penugasan ini sudah dikirim", code=VERDICT_ALREADY_SUBMITTED
    )


def _close_round(db: Session, ronde: ReviewRound, keputusan: RoundDecision) -> None:
    """Round selesai, versi kasus ikut diputuskan (D3 langkah 31/36)."""
    ronde.status = RoundStatus.DECIDED
    ronde.decision = keputusan
    ronde.decided_at = datetime.now(UTC)
    _cases().apply_review_decision(
        db, ronde.case_version_id, approved=keputusan == RoundDecision.APPROVED
    )


def _notify_author(notifier: Notifier, salinan, keputusan: RoundDecision, tugas_round) -> None:
    """AC5: revisi dikirim ke penulis beserta komentar reviewer."""
    if keputusan == RoundDecision.APPROVED:
        judul, isi = f"{salinan.case_code} disetujui", "Kedua reviewer menyetujui kasus ini."
    else:
        komentar = [
            tugas.verdict.comment
            for tugas in tugas_round
            if tugas.status == AssignmentStatus.ACTIVE
            and tugas.verdict is not None
            and tugas.verdict.comment
        ]
        judul, isi = f"{salinan.case_code} perlu revisi", "\n\n".join(komentar)
    notifier.notify(
        [salinan.author_id],
        REVIEW_DECIDED,
        {"title": judul, "body": isi, "link": f"/cases/{salinan.case_id}"},
    )


def list_assigned(db: Session, reviewer_id: uuid.UUID) -> list[AssignedReview]:
    """Dashboard "Perlu Direview" (D3 langkah 17-19).

    Hanya tugas aktif reviewer ini di round yang masih open. Verdict
    reviewer lain tidak pernah ikut dimuat di sini (AC3).
    """
    tugas_list = repository.open_assignments_for(db, reviewer_id)
    salinan = _cases().review_copies(
        db, {tugas.review_round.case_version_id for tugas in tugas_list}
    )
    return [
        AssignedReview(
            assignment_id=tugas.id,
            round_no=tugas.review_round.round_no,
            assigned_at=tugas.assigned_at,
            case=salinan[tugas.review_round.case_version_id],
            my_verdict=_view(tugas.verdict),
        )
        for tugas in tugas_list
    ]


def pending_queue(db: Session, reviewer_id: uuid.UUID) -> list[PendingReview]:
    """Antrean dashboard reviewer (PBI-19 AC6): tugas aktif yang belum dinilai, terlama dulu.

    Sama dengan list_assigned tanpa tugas yang sudah dinilai, dan hanya
    kode serta judul kasus, karena dashboard di-poll tiap menit.
    """
    tugas_list = [
        tugas for tugas in repository.open_assignments_for(db, reviewer_id) if tugas.verdict is None
    ]
    salinan = _cases().review_copies(
        db, {tugas.review_round.case_version_id for tugas in tugas_list}
    )
    return [
        PendingReview(
            assignment_id=tugas.id,
            case_id=tugas.review_round.case_id,
            case_code=salinan[tugas.review_round.case_version_id].case_code,
            title=salinan[tugas.review_round.case_version_id].identity.title,
            round_no=tugas.review_round.round_no,
            waiting_since=tugas.review_round.opened_at,
            assigned_at=tugas.assigned_at,
        )
        for tugas in tugas_list
    ]


def count_cases_waiting(db: Session) -> int:
    """Jumlah kasus menunggu review untuk dashboard (PBI-19 AC1).

    Kasus in_review yang round-nya belum punya verdict sah dari kedua slot
    reviewer (Kak Aileen, 17 Okt). Kasus dengan 0 atau 1 reviewer ikut
    terhitung, jadi angkanya bisa lebih besar dari total antrean reviewer.
    """
    return repository.count_cases_waiting(db, slots=REVIEWERS_PER_ROUND)


def count_cases_needing_reassignment(db: Session) -> int:
    """Jumlah kasus yang perlu penugasan ulang, untuk dashboard Admin (PBI-6 AC8)."""
    return repository.count_cases_needing_reassignment(db)


def get_case_review(db: Session, case_id: uuid.UUID, viewer: Viewer) -> CaseReview:
    """Round terakhir sebuah kasus, dengan verdict yang disaring aturan blind (AC3).

    Hanya assignment aktif yang ditampilkan: verdict dari reviewer yang
    sudah diganti tidak dihitung, jadi juga tidak ditampilkan (AC7).
    """
    author_id = _cases().case_author(db, case_id)
    assigned = viewer.role == Role.REVIEWER and case_id in assigned_case_ids(db, viewer.user_id)
    if not can_open_case_review(viewer, author_id=author_id, assigned=assigned):
        raise ForbiddenError("Anda tidak berwenang melihat review kasus ini")
    ronde = repository.latest_round_for_case(db, case_id)
    if ronde is None:
        raise NotFoundError("Kasus ini belum pernah diajukan untuk direview", code=NO_REVIEW_ROUND)
    aktif = [
        tugas
        for tugas in repository.assignments_in_round(db, ronde.id)
        if tugas.status == AssignmentStatus.ACTIVE
    ]
    names = auth_service.user_names(db, {tugas.reviewer_id for tugas in aktif})
    return CaseReview(
        case_id=ronde.case_id,
        case_version_id=ronde.case_version_id,
        round_no=ronde.round_no,
        status=ronde.status,
        decision=ronde.decision,
        opened_at=ronde.opened_at,
        decided_at=ronde.decided_at,
        reviewers=[
            ReviewerSlot(
                assignment_id=tugas.id,
                reviewer=ReviewerRead(id=tugas.reviewer_id, name=names.get(tugas.reviewer_id, "")),
                status=tugas.status,
                verdict=(
                    _view(tugas.verdict)
                    if can_see_verdict(
                        viewer, owner_id=tugas.reviewer_id, round_status=ronde.status
                    )
                    else None
                ),
            )
            for tugas in aktif
        ],
    )


def _view(verdict: ReviewVerdict | None) -> VerdictView | None:
    if verdict is None:
        return None
    return VerdictView(
        decision=verdict.decision, comment=verdict.comment, submitted_at=verdict.submitted_at
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
    voided: list[ReviewAssignment] = []
    locked_rounds: set[uuid.UUID] = set()
    for assignment in assignments:
        round_id = assignment.review_round_id
        if round_id not in locked_rounds:
            ronde = repository.lock_round(db, round_id)
            locked_rounds.add(round_id)
        else:
            ronde = repository.get_round(db, round_id)

        if ronde is None or ronde.status == RoundStatus.DECIDED or ronde.decision is not None:
            continue

        assignment.status = AssignmentStatus.VOID
        assignment.ended_at = datetime.now(UTC)
        assignment.end_reason = reason
        assignment.review_round = ronde
        ronde.status = RoundStatus.AWAITING_ASSIGNMENT
        voided.append(assignment)

    if voided:
        db.flush()
    return voided


def _clean_reason(reason: str) -> str:
    clean = reason.strip() if reason else ""
    if not clean:
        raise ValidationError(
            "Alasan wajib diisi untuk aksi ini",
            code="REASON_REQUIRED",
            field="reason",
        )
    return clean


def _ensure_assignment_replaceable(assignment: ReviewAssignment | None) -> ReviewAssignment:
    if not assignment:
        raise NotFoundError(ASSIGNMENT_NOT_FOUND)
    if assignment.status == AssignmentStatus.REPLACED:
        raise ConflictError(
            "Penugasan ini sudah pernah diganti",
            code="ASSIGNMENT_ALREADY_REPLACED",
        )
    return assignment


def _ensure_round_not_decided(ronde: ReviewRound) -> None:
    if ronde.status == RoundStatus.DECIDED or ronde.decision is not None:
        raise ConflictError(
            "Putaran review sudah selesai diputuskan",
            code="ROUND_ALREADY_DECIDED",
        )


def _validate_new_reviewer(
    db: Session,
    new_reviewer_id: uuid.UUID,
    ronde: ReviewRound,
    old_assignment: ReviewAssignment,
    active_assignments: list[ReviewAssignment],
) -> None:
    new_user = auth_service.get_user(db, new_reviewer_id)
    if not new_user:
        raise NotFoundError("Reviewer baru tidak ditemukan")

    case_author_id = _cases().case_author(db, ronde.case_id)
    if new_reviewer_id == case_author_id:
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

    other_active_ids = {a.reviewer_id for a in active_assignments if a.id != old_assignment.id}
    if new_reviewer_id in other_active_ids:
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


def _apply_reviewer_replacement(
    db: Session,
    ronde: ReviewRound,
    old_assignment: ReviewAssignment,
    new_reviewer_id: uuid.UUID,
    admin_id: uuid.UUID,
    reason: str,
    active_assignments: list[ReviewAssignment],
) -> ReviewAssignment:
    old_assignment.status = AssignmentStatus.REPLACED
    old_assignment.ended_at = datetime.now(UTC)
    old_assignment.end_reason = reason
    old_assignment.review_round = ronde

    new_assignment = ReviewAssignment(
        review_round_id=ronde.id,
        reviewer_id=new_reviewer_id,
        source=AssignmentSource.ADMIN,
        assigned_by=admin_id,
        status=AssignmentStatus.ACTIVE,
    )
    new_assignment.review_round = ronde
    repository.add_assignment(db, new_assignment)

    remaining_active = [a for a in active_assignments if a.id != old_assignment.id]
    total_active_count = len(remaining_active) + 1
    if total_active_count >= REVIEWERS_PER_ROUND:
        ronde.status = RoundStatus.OPEN
    else:
        ronde.status = RoundStatus.AWAITING_ASSIGNMENT

    return new_assignment


def _notify_replacement(
    notifier: Notifier | None, new_reviewer_id: uuid.UUID, case_id: uuid.UUID
) -> None:
    pemberitahu = notifier if notifier is not None else NoopNotifier()
    pemberitahu.notify(
        [new_reviewer_id],
        REVIEW_ASSIGNED,
        {
            "title": "Tugas review baru",
            "body": "Anda ditugaskan mereview sebuah kasus.",
            "link": f"/cases/{case_id}",
        },
    )


def replace_reviewer(
    db: Session,
    *,
    assignment_id: uuid.UUID,
    new_reviewer_id: uuid.UUID,
    reason: str,
    admin_id: uuid.UUID,
    notifier: Notifier | None = None,
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
    clean_reason = _clean_reason(reason)
    old_assignment = _ensure_assignment_replaceable(repository.get_assignment(db, assignment_id))

    ronde = repository.lock_round(db, old_assignment.review_round_id)

    # Baca ulang sesudah kunci round: assignment dimuat sebelum antre kunci,
    # jadi statusnya bisa sudah 'replaced' bila di-replace oleh transaksi lain.
    old_assignment = _ensure_assignment_replaceable(
        repository.get_assignment(db, assignment_id, fresh=True)
    )
    _ensure_round_not_decided(ronde)

    active_assignments = repository.get_active_assignments_for_round(db, ronde.id)
    _validate_new_reviewer(db, new_reviewer_id, ronde, old_assignment, active_assignments)

    new_assignment = _apply_reviewer_replacement(
        db, ronde, old_assignment, new_reviewer_id, admin_id, clean_reason, active_assignments
    )
    _notify_replacement(notifier, new_reviewer_id, ronde.case_id)

    db.flush()
    db.commit()

    return old_assignment, new_assignment


def get_needs_reassignment(db: Session) -> list[NeedsReassignmentResponse]:
    """Daftar kasus yang memerlukan penugasan reviewer ulang (AC8, SCRUM-147)."""
    rounds = repository.get_rounds_needing_reassignment(db)
    if not rounds:
        return []

    version_ids = {r.case_version_id for r in rounds}
    copies = _cases().review_copies(db, version_ids)

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

        copy = copies.get(ronde.case_version_id)
        if copy is not None:
            case_code = copy.case_code
            case_title = copy.identity.title
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


def _cases():
    """cases.service, diimpor saat dipakai.

    cases.service mengimpor modul ini untuk open_round, jadi impor di
    tingkat modul akan melingkar.
    """
    from app.modules.cases import service as cases_service

    return cases_service
