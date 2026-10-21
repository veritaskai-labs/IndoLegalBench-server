"""K-03: persisted, scoped author replies with atomic audit and notifications."""

import uuid

import pytest

from app.modules.audit.models import AuditLog
from app.modules.notifications.models import Notification
from app.modules.reviews.models import AssignmentStatus, ReviewAssignment
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.reviews.test_review_api import (
    REVIEWER_A,
    REVIEWER_B,
    _nilai,
)
from tests.modules.reviews.test_review_api import (
    jadi as jadi,
)
from tests.modules.reviews.test_review_api import (
    kasus as kasus,
)


@pytest.fixture(autouse=True)
def isolate_existing_notifications(monkeypatch):
    # Existing round setup uses a separate best-effort notifier; isolate it from local DBs.
    monkeypatch.setattr("app.modules.notifications.service.DbNotifier.notify", lambda *args: None)


@pytest.fixture
def thread(kasus, jadi):
    client, case_id, assignments = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    assert (
        _nilai(client, assignments[REVIEWER_A], "revise", "Mohon perjelas rujukan.").status_code
        == 201
    )
    jadi(Role.REVIEWER, REVIEWER_B)
    assert _nilai(client, assignments[REVIEWER_B]).status_code == 201
    jadi(Role.AUTHOR, USER_ID_QA)
    return client, case_id, assignments


def url(assignment):
    return f"/reviews/{assignment}/discussion"


def post(client, assignment, body="  Bagian mana yang perlu diperjelas?  "):
    return client.post(f"/reviews/{assignment}/replies", json={"body": body})


def test_author_reply_persists_and_notifies_only_comment_owner(thread, jadi, db_session):
    client, case_id, ids = thread
    response = post(client, ids[REVIEWER_A])
    assert response.status_code == 201
    reply = response.json()
    assert reply["body"] == "Bagian mana yang perlu diperjelas?"
    assert reply["author_id"] == str(USER_ID_QA)
    assert reply["created_at"]
    discussion = client.get(url(ids[REVIEWER_A])).json()
    assert discussion["comment"] == "Mohon perjelas rujukan."
    assert discussion["replies"] == [reply]
    assert discussion["can_reply"] is True
    [event] = db_session.query(AuditLog).filter_by(action="review.comment_replied").all()
    assert str(event.entity_id) == ids[REVIEWER_A]
    assert str(event.case_id) == case_id
    assert event.actor_user_id == USER_ID_QA
    assert event.after == {"reply_id": reply["id"]}
    [notification] = db_session.query(Notification).filter_by(type="review_comment_replied").all()
    assert notification.user_id == REVIEWER_A
    assert notification.link == url(ids[REVIEWER_A])
    assert reply["body"] not in notification.body
    jadi(Role.REVIEWER, REVIEWER_A)
    mine = client.get(url(ids[REVIEWER_A]))
    assert mine.status_code == 200
    assert mine.json()["replies"] == [reply]
    assert mine.json()["can_reply"] is False


@pytest.mark.parametrize(
    "role, identity",
    [(Role.AUTHOR, uuid.uuid4()), (Role.REVIEWER, REVIEWER_B), (Role.VIEWER, uuid.uuid4())],
)
def test_unrelated_users_cannot_read_or_reply(thread, jadi, role, identity):
    client, _, ids = thread
    jadi(role, identity)
    assert client.get(url(ids[REVIEWER_A])).status_code == 403
    assert post(client, ids[REVIEWER_A]).status_code == 403


def test_admin_and_reviewer_read_only(thread, jadi):
    client, _, ids = thread
    for role, identity in [(Role.ADMIN, uuid.uuid4()), (Role.REVIEWER, REVIEWER_A)]:
        jadi(role, identity)
        assert client.get(url(ids[REVIEWER_A])).status_code == 200
        assert post(client, ids[REVIEWER_A]).status_code == 403


@pytest.mark.parametrize("body", ["", "  \n ", "x" * 5001])
def test_invalid_body_rejected_without_side_effects(thread, db_session, body):
    client, _, ids = thread
    assert post(client, ids[REVIEWER_A], body).status_code == 422
    assert db_session.query(AuditLog).filter_by(action="review.comment_replied").count() == 0
    assert db_session.query(Notification).filter_by(type="review_comment_replied").count() == 0


def test_no_comment_and_missing_assignment(thread):
    client, _, ids = thread
    assert client.get(url(ids[REVIEWER_B])).status_code == 404
    assert post(client, ids[REVIEWER_B]).status_code == 404
    missing = uuid.uuid4()
    assert client.get(url(missing)).status_code == 404
    assert post(client, missing).status_code == 404


def test_blind_review_cannot_be_bypassed(kasus, jadi):
    client, _, ids = kasus
    jadi(Role.REVIEWER, REVIEWER_A)
    _nilai(client, ids[REVIEWER_A], "revise", "Private comment")
    jadi(Role.AUTHOR, USER_ID_QA)
    assert client.get(url(ids[REVIEWER_A])).status_code == 403
    assert post(client, ids[REVIEWER_A]).status_code == 403
    jadi(Role.REVIEWER, REVIEWER_B)
    assert client.get(url(ids[REVIEWER_A])).status_code == 403


def test_replaced_assignment_readable_but_not_writable(thread, db_session):
    client, _, ids = thread
    task = db_session.get(ReviewAssignment, uuid.UUID(ids[REVIEWER_A]))
    task.status = AssignmentStatus.REPLACED
    db_session.commit()
    assert client.get(url(ids[REVIEWER_A])).json()["can_reply"] is False
    assert post(client, ids[REVIEWER_A]).status_code == 409


def test_replies_keep_order_and_maximum_body(thread):
    client, _, ids = thread
    first = post(client, ids[REVIEWER_A], "a" * 5000)
    second = post(client, ids[REVIEWER_A], "Second")
    assert first.status_code == second.status_code == 201
    assert client.get(url(ids[REVIEWER_A])).json()["replies"] == [first.json(), second.json()]
