import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.cases.models import Case, CaseStatus, CaseVersion, SplitTag
from app.modules.notifications.models import Notification
from app.modules.notifications.types import REVIEW_NEEDS_ASSIGNMENT
from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    ReviewAssignment,
    ReviewRound,
    RoundStatus,
)
from app.modules.suites.models import Suite, SuiteStatus
from app.shared.security import Role

REVIEWING_ROLE = Role.REVIEWER

ACTING_ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000199c1")
OTHER_ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000199c2")
RETIRED_ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000199c3")
AUTHOR = uuid.UUID("00000000-0000-0000-0000-0000000199c4")
LEAVING_MEMBER = uuid.UUID("00000000-0000-0000-0000-0000000199c5")
PEER = uuid.UUID("00000000-0000-0000-0000-0000000199c6")
LEAVING_NAME = "Anggota Keluar"


@pytest.fixture(autouse=True)
def notices_land_in_the_test_db(db_session, monkeypatch):
    """Stub: point DbNotifier's own session at the test database."""
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr("app.modules.notifications.service.get_session_factory", lambda: factory)


@pytest.fixture
def suite_id(db_session) -> uuid.UUID:
    """Active and retired admins, an author, the leaving member and a peer."""
    people = [
        (ACTING_ADMIN, Role.ADMIN, "Admin Pelaku", True),
        (OTHER_ADMIN, Role.ADMIN, "Admin Lain", True),
        (RETIRED_ADMIN, Role.ADMIN, "Admin Nonaktif", False),
        (AUTHOR, Role.AUTHOR, "Penulis", True),
        (LEAVING_MEMBER, REVIEWING_ROLE, LEAVING_NAME, True),
        (PEER, REVIEWING_ROLE, "Rekan Reviewer", True),
    ]
    db_session.add_all(
        User(
            id=user_id,
            email=f"{user_id.hex[-4:]}@veritask.test",
            name=name,
            role=role,
            is_active=active,
        )
        for user_id, role, name, active in people
    )
    suite = Suite(name="Suite QA 199 notif", status=SuiteStatus.ACTIVE, created_by=AUTHOR)
    db_session.add(suite)
    db_session.commit()
    return suite.id


def _waiting_case(db_session, suite_id: uuid.UUID, code: str) -> None:
    """A case where the leaving member and the peer still owe a verdict."""
    version_id = uuid.uuid4()
    case = Case(
        case_code=code,
        suite_id=suite_id,
        created_by=AUTHOR,
        updated_by=AUTHOR,
        current_version_id=version_id,
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(
        CaseVersion(
            id=version_id,
            case_id=case.id,
            case_code=code,
            version_no=1,
            split_tag=SplitTag.DEV,
            status=CaseStatus.IN_REVIEW,
            content={"title": "Placeholder QA", "question": "Placeholder QA"},
            created_by=AUTHOR,
        )
    )
    db_session.flush()
    review_round = ReviewRound(
        case_id=case.id, case_version_id=version_id, round_no=1, status=RoundStatus.OPEN
    )
    db_session.add(review_round)
    db_session.flush()
    for reviewer_id in (LEAVING_MEMBER, PEER):
        assignment = ReviewAssignment(
            review_round_id=review_round.id,
            reviewer_id=reviewer_id,
            source=AssignmentSource.SYSTEM,
            status=AssignmentStatus.ACTIVE,
        )
        assignment.review_round = review_round
        db_session.add(assignment)
    db_session.commit()


def _notices(db_session, user_id: uuid.UUID) -> list[Notification]:
    db_session.expire_all()
    return (
        db_session.query(Notification)
        .filter(Notification.user_id == user_id, Notification.type == REVIEW_NEEDS_ASSIGNMENT)
        .all()
    )


def _deactivate(client, confirm: bool = True):
    params = {"confirm": "true"} if confirm else {}
    return client.post(f"/admin/users/{LEAVING_MEMBER}/deactivate", params=params)


def _change_role(client, confirm: bool = True):
    params = {"confirm": "true"} if confirm else {}
    return client.patch(f"/admin/users/{LEAVING_MEMBER}", json={"role": "viewer"}, params=params)


class TestConfirmedReleaseNotifiesEveryActiveAdmin:
    def test_deactivation_with_two_waiting_cases_sends_one_notice_each(
        self, as_role, db_session, suite_id
    ):
        _waiting_case(db_session, suite_id, "QA-199-N1")
        _waiting_case(db_session, suite_id, "QA-199-N2")
        client = as_role(Role.ADMIN, user_id=ACTING_ADMIN)

        assert _deactivate(client).status_code == 200

        for admin_id in (ACTING_ADMIN, OTHER_ADMIN):
            [notice] = _notices(db_session, admin_id)
            assert notice.title == "2 kasus perlu penugasan ulang"
            assert f"{LEAVING_NAME} dinonaktifkan" in notice.body
            assert notice.link == "/admin/reviews"

    def test_role_change_notice_says_the_role_changed(self, as_role, db_session, suite_id):
        _waiting_case(db_session, suite_id, "QA-199-N3")
        client = as_role(Role.ADMIN, user_id=ACTING_ADMIN)

        assert _change_role(client).status_code == 200

        [notice] = _notices(db_session, OTHER_ADMIN)
        assert notice.title == "1 kasus perlu penugasan ulang"
        assert f"Peran {LEAVING_NAME} diubah" in notice.body
        assert "dinonaktifkan" not in notice.body

    def test_acting_admin_reads_the_notice_through_the_api(self, as_role, db_session, suite_id):
        _waiting_case(db_session, suite_id, "QA-199-N4")
        client = as_role(Role.ADMIN, user_id=ACTING_ADMIN)
        assert _deactivate(client).status_code == 200

        inbox = client.get("/notifications").json()["items"]
        released = [item for item in inbox if item["type"] == REVIEW_NEEDS_ASSIGNMENT]
        assert [(item["title"], item["link"]) for item in released] == [
            ("1 kasus perlu penugasan ulang", "/admin/reviews")
        ]


class TestNoticeReachesOnlyActiveAdmins:
    def test_inactive_admin_and_non_admins_get_nothing(self, as_role, db_session, suite_id):
        _waiting_case(db_session, suite_id, "QA-199-N5")
        client = as_role(Role.ADMIN, user_id=ACTING_ADMIN)
        assert _deactivate(client).status_code == 200

        for user_id in (RETIRED_ADMIN, AUTHOR, LEAVING_MEMBER, PEER):
            assert _notices(db_session, user_id) == []


class TestNoReleaseNoNotice:
    @pytest.mark.parametrize("act", [_deactivate, _change_role], ids=["deactivate", "role"])
    def test_confirmed_action_without_waiting_case_sends_nothing(
        self, as_role, db_session, suite_id, act
    ):
        client = as_role(Role.ADMIN, user_id=ACTING_ADMIN)
        assert act(client).status_code == 200
        assert _notices(db_session, ACTING_ADMIN) == []
        assert _notices(db_session, OTHER_ADMIN) == []

    @pytest.mark.parametrize("act", [_deactivate, _change_role], ids=["deactivate", "role"])
    def test_unconfirmed_action_is_409_and_sends_nothing(self, as_role, db_session, suite_id, act):
        _waiting_case(db_session, suite_id, "QA-199-N6")
        client = as_role(Role.ADMIN, user_id=ACTING_ADMIN)
        assert act(client, confirm=False).status_code == 409
        assert _notices(db_session, ACTING_ADMIN) == []
        assert _notices(db_session, OTHER_ADMIN) == []
