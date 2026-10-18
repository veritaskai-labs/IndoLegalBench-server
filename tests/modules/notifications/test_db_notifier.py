"""Test DbNotifier: menulis baris, menelan error, mengabaikan kunci payload ekstra."""

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.notifications.service import DbNotifier
from app.modules.notifications.types import (
    MEASUREMENT_COMPLETED,
    MEASUREMENT_FAILED,
    REVIEW_ASSIGNED,
)
from app.shared.security import Role

USER_A = uuid.UUID("00000000-0000-0000-0000-00000000a0a1")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000a0a2")


def _factory(db_session):
    """Sessionmaker pada engine yang sama dengan fixture db_session (SQLite memory)."""
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)


@pytest.fixture
def users(db_session):
    for user_id, email in ((USER_A, "a@veritask.test"), (USER_B, "b@veritask.test")):
        db_session.add(
            User(id=user_id, email=email, name=email, role=Role.REVIEWER, is_active=True)
        )
    db_session.commit()


def test_db_notifier_menulis_satu_baris_per_user(db_session, users):
    notifier = DbNotifier(session_factory=_factory(db_session))

    notifier.notify(
        [USER_A, USER_B],
        REVIEW_ASSIGNED,
        {
            "title": "Tugas review baru",
            "body": "Anda ditugaskan.",
            "link": "/cases/x",
            "extra": "diabaikan",
        },
    )

    db_session.expire_all()
    baris = db_session.query(Notification).order_by(Notification.user_id).all()
    assert len(baris) == 2
    assert {row.user_id for row in baris} == {USER_A, USER_B}
    assert all(row.type == REVIEW_ASSIGNED for row in baris)
    assert all(row.title == "Tugas review baru" for row in baris)
    assert all(row.link == "/cases/x" for row in baris)


def test_db_notifier_user_kosong_tidak_menulis(db_session, users):
    DbNotifier(session_factory=_factory(db_session)).notify(
        [], REVIEW_ASSIGNED, {"title": "t", "body": "b", "link": "/"}
    )
    assert db_session.query(Notification).count() == 0


def test_db_notifier_menelan_error():
    factory = MagicMock(side_effect=RuntimeError("db down"))
    # Tidak boleh melempar ke pemanggil.
    DbNotifier(session_factory=factory).notify(
        [USER_A], REVIEW_ASSIGNED, {"title": "t", "body": "b", "link": "/"}
    )


def test_tipe_measurement_dicadangkan():
    assert MEASUREMENT_COMPLETED == "measurement_completed"
    assert MEASUREMENT_FAILED == "measurement_failed"
