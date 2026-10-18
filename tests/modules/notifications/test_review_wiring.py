"""DbNotifier sebagai default notifier di alur review (SCRUM-153)."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
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
        db_session.add(User(id=user_id, email=email, name=email, role=role, is_active=True))
    db_session.commit()


class _Store:
    def __init__(self):
        self.ids: dict[uuid.UUID, uuid.UUID] = {}

    def open_queue(self, reviewer_ids):
        return {
            reviewer_id: ReviewerLoad(open_count=0, last_assigned_at=None, total_count=0)
            for reviewer_id in reviewer_ids
        }

    def save_assignments(self, round_id, reviewer_ids):
        self.ids = {reviewer_id: uuid.uuid4() for reviewer_id in reviewer_ids}
        return self.ids

    def mark_needs_assignment(self, round_id):
        return None


def test_assign_default_db_notifier_menulis_baris(db_session, reviewers, monkeypatch):
    # Arrange
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: _factory(db_session),
    )

    store = _Store()

    # Act
    terpilih = service.assign_on_submit(
        db_session,
        round_id=uuid.uuid4(),
        author_id=AUTHOR,
        store=store,
    )

    # Assert
    assert sorted(terpilih) == sorted([R1, R2])
    db_session.expire_all()
    baris = db_session.query(Notification).all()
    assert len(baris) == 2
    assert {row.user_id for row in baris} == {R1, R2}
    assert all(row.type == "review_assigned" for row in baris)
    assert {row.user_id: row.link for row in baris} == {
        reviewer_id: f"/reviews/{assignment_id}" for reviewer_id, assignment_id in store.ids.items()
    }


def test_submit_verdict_default_db_notifier_menulis_ke_author(db_session, reviewers, monkeypatch):
    # Arrange
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

    # Act
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

    # Assert
    db_session.expire_all()
    baris = db_session.query(Notification).all()
    assert len(baris) == 1
    assert baris[0].user_id == AUTHOR
    assert baris[0].type == "review_decided"
    assert "disetujui" in baris[0].title
    assert ronde.decision == RoundDecision.APPROVED


def test_resume_default_db_notifier_memberitahu_perevisi(db_session, reviewers, monkeypatch):
    # Arrange
    monkeypatch.setattr(
        "app.modules.notifications.service.get_session_factory",
        lambda: _factory(db_session),
    )
    versi = uuid.uuid4()
    sebelumnya = SimpleNamespace(
        id=uuid.uuid4(),
        status=RoundStatus.DECIDED,
        decision=RoundDecision.REVISE,
    )
    approve = SimpleNamespace(
        status=AssignmentStatus.ACTIVE,
        reviewer_id=R1,
        verdict=SimpleNamespace(id=uuid.uuid4(), decision=VerdictDecision.APPROVE, comment=None),
    )
    revise = SimpleNamespace(
        status=AssignmentStatus.ACTIVE,
        reviewer_id=R2,
        verdict=SimpleNamespace(
            id=uuid.uuid4(), decision=VerdictDecision.REVISE, comment="perbaiki"
        ),
    )

    dibuat: dict[uuid.UUID, SimpleNamespace] = {}

    # Act
    with patch.object(service, "repository") as repo:
        repo.latest_round.return_value = sebelumnya
        repo.max_round_no.return_value = 1
        repo.assignments_in_round.return_value = [approve, revise]
        repo.add_round.side_effect = lambda db, ronde: setattr(ronde, "id", uuid.uuid4())
        repo.add_verdict.return_value = None

        with patch.object(service, "_tugaskan") as tugaskan:
            tugaskan.side_effect = lambda db, ronde, reviewer_id: dibuat.setdefault(
                reviewer_id, SimpleNamespace(id=uuid.uuid4(), reviewer_id=reviewer_id)
            )
            with patch.object(service, "_bawa_approve"):
                service.resume_after_revision(
                    db_session,
                    case_id=CASE_ID,
                    case_version_id=versi,
                )

    # Assert
    db_session.expire_all()
    baris = db_session.query(Notification).all()
    assert len(baris) == 1
    assert baris[0].user_id == R2
    assert baris[0].type == "review_assigned"
    assert baris[0].link == f"/reviews/{dibuat[R2].id}"
