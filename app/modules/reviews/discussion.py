"""K-03 discussion rules, separate from verdict submission and round decisions."""

import uuid
from datetime import UTC

from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.notifications import service as notifications
from app.modules.reviews import repository
from app.modules.reviews.models import AssignmentStatus, ReviewCommentReply, RoundStatus
from app.modules.reviews.schemas import CommentDiscussion, CommentReplyRead, CommentReplyWrite
from app.modules.reviews.visibility import Viewer, can_see_verdict
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.shared.security import Role


def _context(db: Session, assignment_id: uuid.UUID, viewer: Viewer):
    from app.modules.cases import service as cases

    task = repository.get_assignment(db, assignment_id)
    if task is None:
        raise NotFoundError("Penugasan review tidak ditemukan")
    author_id = cases.case_author(db, task.review_round.case_id)
    is_author = viewer.role == Role.AUTHOR and viewer.user_id == author_id
    allowed = (
        viewer.role == Role.ADMIN
        or is_author
        or (viewer.role == Role.REVIEWER and viewer.user_id == task.reviewer_id)
    )
    if not allowed or not can_see_verdict(
        viewer, owner_id=task.reviewer_id, round_status=task.review_round.status
    ):
        raise ForbiddenError("Anda tidak berwenang melihat diskusi ini")
    if task.verdict is None or not task.verdict.comment:
        raise NotFoundError("Komentar peninjau belum tersedia", code="REVIEW_COMMENT_NOT_FOUND")
    return task, is_author


def _reply_view(row: ReviewCommentReply, names: dict) -> CommentReplyRead:
    return CommentReplyRead(
        id=row.id,
        author_id=row.author_id,
        author_name=names.get(row.author_id, ""),
        author_role=row.author_role,
        body=row.body,
        created_at=row.created_at,
    )


def read(db: Session, assignment_id: uuid.UUID, viewer: Viewer) -> CommentDiscussion:
    task, is_author = _context(db, assignment_id, viewer)
    rows = repository.comment_replies(db, task.id)
    names = auth_service.user_names(db, {task.reviewer_id, *(r.author_id for r in rows)})
    return CommentDiscussion(
        round_no=task.review_round.round_no,
        round_status=task.review_round.status,
        viewer_id=viewer.user_id,
        commented_at=task.verdict.submitted_at.replace(tzinfo=UTC)
        if task.verdict.submitted_at.tzinfo is None
        else task.verdict.submitted_at,
        assignment_id=task.id,
        case_id=task.review_round.case_id,
        reviewer_name=names.get(task.reviewer_id, ""),
        comment=task.verdict.comment,
        can_reply=(is_author or viewer.role == Role.REVIEWER)
        and task.status == AssignmentStatus.ACTIVE
        and task.review_round.status == RoundStatus.DECIDED,
        replies=[_reply_view(row, names) for row in rows],
    )


def reply(
    db: Session, assignment_id: uuid.UUID, viewer: Viewer, payload: CommentReplyWrite
) -> CommentReplyRead:
    from app.modules.cases import service as cases

    task, is_author = _context(db, assignment_id, viewer)
    if not is_author and viewer.role != Role.REVIEWER:
        raise ForbiddenError(
            "Hanya penulis kasus dan peninjau pemilik komentar yang boleh membalas"
        )
    repository.lock_round(db, task.review_round_id)
    task = repository.get_assignment(db, assignment_id, fresh=True)
    if task.status != AssignmentStatus.ACTIVE:
        raise ConflictError("Penugasan sudah diganti atau dibatalkan", code="ASSIGNMENT_NOT_ACTIVE")
    if task.review_round.status != RoundStatus.DECIDED:
        raise ConflictError(
            "Diskusi dibuka setelah kedua penilaian selesai", code="ROUND_NOT_DECIDED"
        )
    row = ReviewCommentReply(
        assignment_id=task.id,
        author_id=viewer.user_id,
        author_role=viewer.role.value,
        body=payload.body,
    )
    db.add(row)
    db.flush()
    recipient_id = (
        task.reviewer_id if is_author else cases.case_author(db, task.review_round.case_id)
    )
    notifications.enqueue_review_reply(
        db, recipient_id=recipient_id, assignment_id=task.id, sender_role=viewer.role
    )
    result = _reply_view(row, auth_service.user_names(db, {viewer.user_id}))
    db.commit()
    return result
