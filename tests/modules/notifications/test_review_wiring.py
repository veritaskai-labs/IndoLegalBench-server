"""DbNotifier terpasang sebagai default di assign_on_submit dan submit_verdict."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.notifications.service import DbNotifier
from app.modules.notifications.types import REVIEW_ASSIGNED, REVIEW_DECIDED
from app.modules.reviews import service
from app.modules.reviews.models import (
    AssignmentStatus,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)
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
        db_session.add(
            User(id=user_id, email=email, name=email, role=role, is_active=True)
        )
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
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: _factory(db_session),
    )

    terpilih = service.assign_on_submit(
        db_session,
        round_id=uuid.uuid4(),
        case_id=CASE_ID,
        author_id=AUTHOR,
        store=_Store(),
    )

    assert sorted(terpilih) == sorted([R1, R2])
    db_session.expire_all()
    baris = db_session.query(Notification).all()
    assert len(baris) == 2
    assert {row.user_id for row in baris} == {R1, R2}
    assert all(row.type == REVIEW_ASSIGNED for row in baris)
    assert all(row.link == f"/cases/{CASE_ID}" for row in baris)


def test_submit_verdict_default_db_notifier_menulis_ke_author(db_session, reviewers, monkeypatch):
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: _factory(db_session),
    )

    ronde = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=CASE_ID,
        case_version_id=uuid.uuid4(),
        status=RoundStatus.OPEN,
        decision=None,
        decided_at=None,
    )
    tugas = SimpleNamespace(
        id=uuid.uuid4(),
        reviewer_id=R1,
        review_round_id=ronde.id,
        review_round=ronde,
        status=AssignmentStatus.ACTIVE,
        verdict=None,
    )
    rekan = SimpleNamespace(
        reviewer_id=R2,
        status=AssignmentStatus.ACTIVE,
        verdict=SimpleNamespace(decision=VerdictDecision.APPROVE, comment=None),
    )
    salinan = SimpleNamespace(case_id=CASE_ID, case_code="PHK-153", author_id=AUTHOR)

    with (
        patch.object(service, "repository") as repo,
        patch.object(service, "_cases") as cases,
    ):
        repo.get_assignment.return_value = tugas
        repo.lock_round.return_value = ronde
        repo.add_verdict.side_effect = lambda db, verdict: setattr(tugas, "verdict", verdict)
        repo.assignments_in_round.side_effect = lambda db, round_id: [tugas, rekan]
        cases.return_value.review_copies.return_value = {ronde.case_version_id: salinan}

        service.submit_verdict(
            MagicMock(),
            tugas.id,
            reviewer_id=R1,
            decision=VerdictDecision.APPROVE,
            comment=None,
        )

    db_session.expire_all()
    baris = db_session.query(Notification).all()
    assert len(baris) == 1
    assert baris[0].user_id == AUTHOR
    assert baris[0].type == REVIEW_DECIDED
    assert "disetujui" in baris[0].title
    assert ronde.decision == RoundDecision.APPROVED


def test_default_notifier_adalah_db_notifier():
    assert isinstance(DbNotifier(), DbNotifier)
