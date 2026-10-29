"""K-03 against the migrated PostgreSQL schema; isolated disposable CI data."""

import os

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog
from app.modules.cases.models import Case
from app.modules.notifications.models import Notification
from app.modules.reviews import service
from app.modules.reviews.models import (
    ReviewCommentReply,
    ReviewRound,
    ReviewVerdict,
    RoundStatus,
    VerdictDecision,
)
from app.modules.reviews.schemas import CommentReplyWrite
from app.modules.reviews.visibility import Viewer
from app.shared import request_context
from app.shared.security import Role
from tests.integration.test_review_concurrency_pg import _round_siap_dinilai
from tests.integration.test_review_concurrency_pg import buat_sesi as buat_sesi

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL tidak diisi"
    ),
]


def test_reply_audit_notification_committed_together_in_migrated_postgres(buat_sesi):
    case_id, round_id, tasks = _round_siap_dinilai(buat_sesi)
    reviewer_id, assignment_id = next(iter(tasks.items()))
    with buat_sesi() as db:
        author_id = db.get(Case, case_id).created_by
        db.get(ReviewRound, round_id).status = RoundStatus.DECIDED
        db.add(
            ReviewVerdict(
                assignment_id=assignment_id,
                decision=VerdictDecision.REVISE,
                comment="Please clarify",
            )
        )
        db.commit()
        request_context.bind(db, user_id=author_id, role=Role.CONTRIBUTOR, request_id=None)
        result = service.reply_to_comment(
            db,
            assignment_id,
            Viewer(author_id, Role.CONTRIBUTOR),
            CommentReplyWrite(body="Which section?"),
        )
    with buat_sesi() as db:
        assert db.get(ReviewCommentReply, result.id).body == "Which section?"
        event = (
            db.query(AuditLog)
            .filter_by(action="review.comment_replied", entity_id=assignment_id)
            .one()
        )
        assert event.after == {"reply_id": str(result.id)}
        assert event.actor_user_id == author_id
        notice = (
            db.query(Notification)
            .filter_by(
                user_id=reviewer_id,
                type="review_comment_replied",
                link=f"/reviews/{assignment_id}/discussion",
            )
            .one()
        )
        assert notice.body
        assert (
            service.read_comment_discussion(
                db, assignment_id, Viewer(reviewer_id, Role.CONTRIBUTOR)
            )
            .replies[0]
            .id
            == result.id
        )


def test_migrated_reply_constraint_rejects_blank_body(buat_sesi):
    _, _, tasks = _round_siap_dinilai(buat_sesi)
    reviewer_id, assignment_id = next(iter(tasks.items()))
    with buat_sesi() as db:
        db.add(ReviewCommentReply(assignment_id=assignment_id, author_id=reviewer_id, body=" "))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_reviewer_response_targets_author_and_keeps_sender_identity(buat_sesi):
    case_id, round_id, tasks = _round_siap_dinilai(buat_sesi)
    reviewer_id, assignment_id = next(iter(tasks.items()))
    with buat_sesi() as db:
        author_id = db.get(Case, case_id).created_by
        db.get(ReviewRound, round_id).status = RoundStatus.DECIDED
        db.get(ReviewRound, round_id).round_no = 3
        db.add(
            ReviewVerdict(
                assignment_id=assignment_id, decision=VerdictDecision.REVISE, comment="Clarify"
            )
        )
        db.commit()
        request_context.bind(db, user_id=reviewer_id, role=Role.CONTRIBUTOR, request_id=None)
        result = service.reply_to_comment(
            db,
            assignment_id,
            Viewer(reviewer_id, Role.CONTRIBUTOR),
            CommentReplyWrite(body="Please clarify the cited article."),
        )
    with buat_sesi() as db:
        row = db.get(ReviewCommentReply, result.id)
        assert row.author_role == "reviewer"
        assert row.author_id == reviewer_id
        thread = service.read_comment_discussion(
            db, assignment_id, Viewer(author_id, Role.CONTRIBUTOR)
        )
        assert thread.round_no == 3
        # PBI-24: penulis dan reviewer sama-sama Kontributor; identitas lewat user id.
        assert thread.replies[0].author_id == reviewer_id
        assert thread.replies[0].author_name
        assert thread.replies[0].author_role == "reviewer"
        assert (
            db.query(Notification)
            .filter_by(
                user_id=author_id,
                type="review_comment_replied",
                link=f"/reviews/{assignment_id}/discussion",
            )
            .one()
            .title
            == "Balasan peninjau pada diskusi Anda"
        )
        assert (
            db.query(AuditLog)
            .filter_by(action="review.comment_replied", entity_id=assignment_id)
            .one()
            .actor_user_id
            == reviewer_id
        )


def test_migrated_sender_role_default_and_constraint(buat_sesi):
    from sqlalchemy import text

    _, _, tasks = _round_siap_dinilai(buat_sesi)
    reviewer_id, assignment_id = next(iter(tasks.items()))
    with buat_sesi() as db:
        role = db.execute(
            text(
                "INSERT INTO review_comment_replies (id, assignment_id, author_id, body, created_at) VALUES (gen_random_uuid(), :assignment, :author, 'Legacy reply', now()) RETURNING author_role"
            ),
            {"assignment": assignment_id, "author": reviewer_id},
        ).scalar_one()
        assert role == "author"
        db.rollback()
        db.add(
            ReviewCommentReply(
                assignment_id=assignment_id,
                author_id=reviewer_id,
                author_role="admin",
                body="Invalid role",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
