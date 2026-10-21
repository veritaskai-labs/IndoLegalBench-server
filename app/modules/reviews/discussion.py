"""K-03 discussion rules, separate from verdict submission and round decisions."""

import uuid

from sqlalchemy.orm import Session

from app.modules.auth import service as auth_service
from app.modules.notifications import service as notifications
from app.modules.reviews import repository
from app.modules.reviews.models import AssignmentStatus, ReviewCommentReply
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


def read(db: Session, assignment_id: uuid.UUID, viewer: Viewer) -> CommentDiscussion:
    task, is_author = _context(db, assignment_id, viewer)
    names = auth_service.user_names(db, {task.reviewer_id})
    return CommentDiscussion(
        assignment_id=task.id,
        case_id=task.review_round.case_id,
        reviewer_name=names.get(task.reviewer_id, ""),
        comment=task.verdict.comment,
        can_reply=is_author and task.status == AssignmentStatus.ACTIVE,
        replies=[
            CommentReplyRead.model_validate(row) for row in repository.comment_replies(db, task.id)
        ],
    )


def reply(
    db: Session, assignment_id: uuid.UUID, viewer: Viewer, payload: CommentReplyWrite
) -> CommentReplyRead:
    task, is_author = _context(db, assignment_id, viewer)
    if not is_author:
        raise ForbiddenError("Hanya penulis kasus yang boleh membalas komentar")
    # Serialize with replacement/deactivation so a stale author form cannot notify an old slot.
    repository.lock_round(db, task.review_round_id)
    task = repository.get_assignment(db, assignment_id, fresh=True)
    if task.status != AssignmentStatus.ACTIVE:
        raise ConflictError("Penugasan sudah diganti atau dibatalkan", code="ASSIGNMENT_NOT_ACTIVE")
    row = ReviewCommentReply(assignment_id=task.id, author_id=viewer.user_id, body=payload.body)
    db.add(row)
    db.flush()
    notifications.enqueue_review_reply(db, reviewer_id=task.reviewer_id, assignment_id=task.id)
    result = CommentReplyRead.model_validate(row)
    db.commit()
    return result
