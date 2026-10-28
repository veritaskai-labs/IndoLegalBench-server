"""SCRUM-199 (PBI-24 QA) for SCRUM-197 AC8: confirm before dropping a reviewer.

Changing the role of, or deactivating, a member who still has a review
waiting must not silently void that review. Without confirmation the API
answers 409 CONFIRMATION_REQUIRED and nothing changes.

The confirm=true path and the affected-case count field are not in the
contract yet, so they are left for when SCRUM-197 defines them.

Input space:
- action: change role (to every non-reviewer role) | deactivate
- target: reviewer with an active assignment in an open round
- confirmation: not sent
"""

import uuid

import pytest

from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    RoundStatus,
)
from app.modules.suites.models import Suite, SuiteStatus
from app.shared.security import Role

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-0000000199a1")
AUTHOR_ID = uuid.UUID("00000000-0000-0000-0000-0000000199a2")
BUSY_REVIEWER_ID = uuid.UUID("00000000-0000-0000-0000-0000000199b1")
OTHER_REVIEWER_ID = uuid.UUID("00000000-0000-0000-0000-0000000199b2")


def _member(user_id: uuid.UUID, role: Role, label: str) -> User:
    return User(id=user_id, email=f"{label}@veritask.test", name=label.title(), role=role)


@pytest.fixture
def open_review(db_session) -> dict:
    """One case in review with two active reviewers, nothing decided yet."""
    db_session.add_all(
        [
            _member(ADMIN_ID, Role.ADMIN, "admin199"),
            _member(AUTHOR_ID, Role.AUTHOR, "author199"),
            _member(BUSY_REVIEWER_ID, Role.REVIEWER, "busy199"),
            _member(OTHER_REVIEWER_ID, Role.REVIEWER, "other199"),
        ]
    )
    suite = Suite(name="Suite QA 199", status=SuiteStatus.ACTIVE, created_by=AUTHOR_ID)
    db_session.add(suite)
    db_session.flush()

    version_id = uuid.uuid4()
    case = Case(
        case_code="QA-199",
        suite_id=suite.id,
        created_by=AUTHOR_ID,
        updated_by=AUTHOR_ID,
        current_version_id=version_id,
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(
        CaseVersion(
            id=version_id,
            case_id=case.id,
            case_code="QA-199",
            version_no=1,
            split_tag=SplitTag.DEV,
            status=CaseStatus.IN_REVIEW,
            content={"title": "Placeholder QA", "question": "Placeholder QA"},
            created_by=AUTHOR_ID,
        )
    )
    db_session.flush()

    review_round = ReviewRound(
        case_id=case.id, case_version_id=version_id, round_no=1, status=RoundStatus.OPEN
    )
    db_session.add(review_round)
    db_session.flush()
    assignments = [
        ReviewAssignment(
            review_round_id=review_round.id,
            reviewer_id=reviewer_id,
            source=AssignmentSource.SYSTEM,
            status=AssignmentStatus.ACTIVE,
        )
        for reviewer_id in (BUSY_REVIEWER_ID, OTHER_REVIEWER_ID)
    ]
    for assignment in assignments:
        assignment.review_round = review_round
    db_session.add_all(assignments)
    db_session.commit()
    return {"round_id": review_round.id, "busy_assignment_id": assignments[0].id}


def _assert_nothing_changed(db_session, open_review: dict) -> None:
    db_session.expire_all()
    reviewer = db_session.get(User, BUSY_REVIEWER_ID)
    assert reviewer.role == Role.REVIEWER
    assert reviewer.is_active is True
    assignment = db_session.get(ReviewAssignment, open_review["busy_assignment_id"])
    assert assignment.status == AssignmentStatus.ACTIVE
    assert db_session.get(ReviewRound, open_review["round_id"]).status == RoundStatus.OPEN


class TestBusyReviewerNeedsConfirmation:
    @pytest.mark.parametrize("new_role", ["viewer", "author", "admin"])
    def test_role_change_without_confirm_is_409_and_nothing_changes(
        self, as_role, db_session, open_review, new_role
    ):
        client = as_role(Role.ADMIN, user_id=ADMIN_ID)
        response = client.patch(f"/admin/users/{BUSY_REVIEWER_ID}", json={"role": new_role})
        assert response.status_code == 409
        assert response.json()["code"] == "CONFIRMATION_REQUIRED"
        _assert_nothing_changed(db_session, open_review)

    def test_deactivate_without_confirm_is_409_and_nothing_changes(
        self, as_role, db_session, open_review
    ):
        client = as_role(Role.ADMIN, user_id=ADMIN_ID)
        response = client.post(f"/admin/users/{BUSY_REVIEWER_ID}/deactivate")
        assert response.status_code == 409
        assert response.json()["code"] == "CONFIRMATION_REQUIRED"
        _assert_nothing_changed(db_session, open_review)
