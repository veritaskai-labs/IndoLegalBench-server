"""Logika bisnis modul reviews.

PBI-6 Review independen sebelum kasus disetujui. Ini satu-satunya pintu
masuk yang boleh dipanggil modul lain. Service tidak boleh menyentuh HTTP.

SCRUM-143 menyiapkan round untuk pengajuan kasus dan daftar kasus milik
reviewer. SCRUM-144 memilih dua reviewer. Fungsi yang dipanggil dari
dalam pengajuan kasus tidak pernah commit: cases.service memegang
transaksinya.

SCRUM-145 menerima verdict dan memutuskan round. submit_verdict adalah
aksi tingkat atas, jadi ia yang commit. Aturan keputusan ada di
decision.py, aturan blind di visibility.py.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.audit import service as audit_service
from app.modules.auth import service as auth_service
from app.modules.reviews import repository
from app.modules.reviews.decision import counted_decisions, decide
from app.modules.reviews.models import (
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
VERDICT_ALREADY_SUBMITTED = "VERDICT_ALREADY_SUBMITTED"
NO_REVIEW_ROUND = "NO_REVIEW_ROUND"

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
        raise NotFoundError("Penugasan review tidak ditemukan")
    if tugas.reviewer_id != reviewer_id:
        raise ForbiddenError("Penugasan ini milik reviewer lain", code=NOT_YOUR_ASSIGNMENT)
    salinan = _cases().review_copies(db, {tugas.round.case_version_id})[tugas.round.case_version_id]
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
        _notify_author(notifier or NoopNotifier(), salinan, keputusan, tugas_round)
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
    salinan = _cases().review_copies(db, {tugas.round.case_version_id for tugas in tugas_list})
    return [
        AssignedReview(
            assignment_id=tugas.id,
            round_no=tugas.round.round_no,
            assigned_at=tugas.assigned_at,
            case=salinan[tugas.round.case_version_id],
            my_verdict=_view(tugas.verdict),
        )
        for tugas in tugas_list
    ]


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


def _cases():
    """cases.service, diimpor saat dipakai.

    cases.service mengimpor modul ini untuk open_round, jadi impor di
    tingkat modul akan melingkar.
    """
    from app.modules.cases import service as cases_service

    return cases_service
