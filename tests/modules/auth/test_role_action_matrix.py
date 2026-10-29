import uuid

import pytest

from app.modules.auth.models import User
from app.modules.reviews.models import ReviewAssignment, ReviewRound, ReviewVerdict
from app.shared.security import Role
from tests.modules.cases.test_cases import _badan, _suite
from tests.modules.conftest import USER_ID_QA
from tests.modules.reviews.test_review_api import _nilai, _status_versi
from tests.modules.reviews.test_review_api import jadi as jadi

CONTRIBUTOR_A = uuid.UUID("00000000-0000-0000-0000-0000000199d1")
CONTRIBUTOR_B = uuid.UUID("00000000-0000-0000-0000-0000000199d2")
ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000199d3")
VIEWER_ID = uuid.UUID("00000000-0000-0000-0000-0000000199d4")
DEVELOPER_AI_ID = uuid.UUID("00000000-0000-0000-0000-0000000199d5")

NOT_WRITERS = [(Role.VIEWER, VIEWER_ID), (Role.DEVELOPER_AI, DEVELOPER_AI_ID)]
NOT_WRITER_IDS = ["viewer", "developer-ai"]


@pytest.fixture(autouse=True)
def no_assignment_notices(monkeypatch):
    # Assignment notices open their own session and are not under test here.
    monkeypatch.setattr("app.modules.notifications.service.DbNotifier.notify", lambda *a: None)


@pytest.fixture
def draft(as_role, db_session):
    """A draft case written by contributor USER_ID_QA, plus every other role."""
    client = as_role(Role.CONTRIBUTOR)
    people = [
        (USER_ID_QA, Role.CONTRIBUTOR),
        (CONTRIBUTOR_A, Role.CONTRIBUTOR),
        (CONTRIBUTOR_B, Role.CONTRIBUTOR),
        (ADMIN_ID, Role.ADMIN),
        (VIEWER_ID, Role.VIEWER),
        (DEVELOPER_AI_ID, Role.DEVELOPER_AI),
    ]
    db_session.add_all(
        User(id=uid, email=f"{uid.hex[-4:]}@veritask.test", name=uid.hex[-4:], role=role)
        for uid, role in people
    )
    db_session.commit()
    suite_id = _suite(client)
    created = client.post(f"/suites/{suite_id}/cases", json=_badan())
    assert created.status_code == 201
    return client, suite_id, created.json()["id"]


@pytest.fixture
def submitted(draft, db_session):
    """The draft once submitted; maps each reviewer id to its assignment."""
    client, _, case_id = draft
    assert client.post(f"/cases/{case_id}/submit-review").status_code == 200
    review_round = db_session.query(ReviewRound).filter_by(case_id=uuid.UUID(case_id)).one()
    rows = db_session.query(ReviewAssignment).filter_by(review_round_id=review_round.id)
    return client, case_id, {row.reviewer_id: str(row.id) for row in rows}


class TestWritingIsForContributorsAndAdmins:
    @pytest.mark.parametrize(
        ("role", "user_id"),
        [(Role.CONTRIBUTOR, CONTRIBUTOR_A), (Role.ADMIN, ADMIN_ID)],
        ids=["contributor", "admin"],
    )
    def test_writer_creates_suite_and_case(self, draft, jadi, role, user_id):
        client, _, _ = draft
        jadi(role, user_id)
        suite_id = _suite(client, name=f"Suite {role.value}")
        created = client.post(f"/suites/{suite_id}/cases", json=_badan(case_code="PHK-002"))
        assert created.status_code == 201

    @pytest.mark.parametrize(("role", "user_id"), NOT_WRITERS, ids=NOT_WRITER_IDS)
    def test_non_writer_cannot_create_suite(self, draft, jadi, role, user_id):
        client, _, _ = draft
        jadi(role, user_id)
        response = client.post("/suites", json={"name": "Suite liar", "description": "x"})
        assert response.status_code == 403

    @pytest.mark.parametrize(("role", "user_id"), NOT_WRITERS, ids=NOT_WRITER_IDS)
    def test_non_writer_cannot_create_case(self, draft, jadi, role, user_id):
        client, suite_id, _ = draft
        jadi(role, user_id)
        response = client.post(f"/suites/{suite_id}/cases", json=_badan(case_code="PHK-003"))
        assert response.status_code == 403

    @pytest.mark.parametrize(("role", "user_id"), NOT_WRITERS, ids=NOT_WRITER_IDS)
    def test_non_writer_cannot_edit_a_draft(self, draft, jadi, role, user_id):
        client, _, case_id = draft
        jadi(role, user_id)
        response = client.put(f"/cases/{case_id}", json=_badan(case_code="PHK-001"))
        assert response.status_code == 403


class TestOnlyTheAuthorSubmits:
    @pytest.mark.parametrize(
        ("role", "user_id"),
        [
            (Role.CONTRIBUTOR, CONTRIBUTOR_A),
            (Role.ADMIN, ADMIN_ID),
            (Role.VIEWER, VIEWER_ID),
            (Role.DEVELOPER_AI, DEVELOPER_AI_ID),
        ],
        ids=["other-contributor", "admin", "viewer", "developer-ai"],
    )
    def test_anyone_but_the_author_is_refused(self, draft, jadi, db_session, role, user_id):
        client, _, case_id = draft
        jadi(role, user_id)
        assert client.post(f"/cases/{case_id}/submit-review").status_code == 403
        assert _status_versi(db_session, case_id) == "draft"
        assert db_session.query(ReviewRound).count() == 0


class TestOnlyAnAssignedContributorGivesAVerdict:
    @pytest.mark.parametrize(
        ("role", "user_id"),
        [
            (Role.ADMIN, ADMIN_ID),
            (Role.VIEWER, VIEWER_ID),
            (Role.DEVELOPER_AI, DEVELOPER_AI_ID),
        ],
        ids=["admin", "viewer", "developer-ai"],
    )
    def test_non_reviewer_role_cannot_decide(self, submitted, jadi, db_session, role, user_id):
        client, case_id, assignments = submitted
        jadi(role, user_id)
        assignment_id = next(iter(assignments.values()))
        assert _nilai(client, assignment_id).status_code == 403
        assert _status_versi(db_session, case_id) == "in_review"
        assert db_session.query(ReviewVerdict).count() == 0
