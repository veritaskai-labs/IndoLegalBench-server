"""DbNotifier sebagai default notifier di alur review (SCRUM-153)."""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.reviews import service
from app.modules.reviews.ports import ReviewerLoad
from app.shared.security import Role

CASE_ID = uuid.UUID("00000000-0000-0000-0000-0000000153c1")
AUTHOR = uuid.UUID("00000000-0000-0000-0000-0000000153a1")
R1 = uuid.UUID("00000000-0000-0000-0000-000000015301")
R2 = uuid.UUID("00000000-0000-0000-0000-000000015302")


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)


@pytest.fixture
def reviewers(db_session):
    for user_id, email, role in (
        (AUTHOR, "author@veritask.test", Role.AUTHOR),
        (R1, "r1@veritask.test", Role.REVIEWER),
        (R2, "r2@veritask.test", Role.REVIEWER),
    ):
        db_session.add(User(id=user_id, email=email, name=email, role=role, is_active=True))
    db_session.commit()


class _Store:
    def open_queue(self, reviewer_ids):
        return {
            reviewer_id: ReviewerLoad(open_count=0, last_assigned_at=None, total_count=0)
            for reviewer_id in reviewer_ids
        }

    def save_assignments(self, round_id, reviewer_ids):
        return None

    def mark_needs_assignment(self, round_id):
        return None


def test_assign_default_db_notifier_menulis_baris(db_session, reviewers, monkeypatch):
    # Arrange
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: _factory(db_session),
    )

    # Act
    terpilih = service.assign_on_submit(
        db_session,
        round_id=uuid.uuid4(),
        case_id=CASE_ID,
        author_id=AUTHOR,
        store=_Store(),
    )

    # Assert
    assert sorted(terpilih) == sorted([R1, R2])
    db_session.expire_all()
    baris = db_session.query(Notification).all()
    assert len(baris) == 2
    assert {row.user_id for row in baris} == {R1, R2}
    assert all(row.type == "review_assigned" for row in baris)
    assert all(row.link == f"/cases/{CASE_ID}" for row in baris)
