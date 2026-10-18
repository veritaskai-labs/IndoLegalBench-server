"""DbNotifier menulis baris notifikasi (SCRUM-153).

Sesi pendek sendiri, satu baris per user dari title/body/link, dan
tidak pernah melempar ke pemanggil.
"""

import logging
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.notifications.service import DbNotifier
from app.shared.security import Role

USER_A = uuid.UUID("00000000-0000-0000-0000-00000000a0a1")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000a0a2")


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)


@pytest.fixture
def users(db_session):
    for user_id, email in ((USER_A, "a@veritask.test"), (USER_B, "b@veritask.test")):
        db_session.add(
            User(id=user_id, email=email, name=email, role=Role.REVIEWER, is_active=True)
        )
    db_session.commit()


def test_db_notifier_menulis_satu_baris_per_user(db_session, users):
    # Arrange
    notifier = DbNotifier(session_factory=_factory(db_session))

    # Act
    notifier.notify(
        [USER_A, USER_B],
        "review_assigned",
        {
            "title": "Tugas review baru",
            "body": "Anda ditugaskan.",
            "link": "/cases/x",
            "extra": "diabaikan",
        },
    )

    # Assert
    db_session.expire_all()
    baris = db_session.query(Notification).order_by(Notification.user_id).all()
    assert len(baris) == 2
    assert {row.user_id for row in baris} == {USER_A, USER_B}
    assert all(row.type == "review_assigned" for row in baris)
    assert all(row.title == "Tugas review baru" for row in baris)
    assert all(row.body == "Anda ditugaskan." for row in baris)
    assert all(row.link == "/cases/x" for row in baris)


def test_db_notifier_user_kosong_tidak_menulis(db_session, users):
    # Arrange / Act
    DbNotifier(session_factory=_factory(db_session)).notify(
        [], "review_assigned", {"title": "t", "body": "b", "link": "/"}
    )

    # Assert
    assert db_session.query(Notification).count() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"body": "b", "link": "/"},
        {"title": "t", "link": "/"},
        {"title": "t", "body": "b"},
        {"title": "  ", "body": "b", "link": "/"},
    ],
    ids=["tanpa-title", "tanpa-body", "tanpa-link", "title-kosong"],
)
def test_db_notifier_payload_tidak_lengkap_tidak_menulis_dan_dicatat(
    db_session, users, payload, caplog
):
    # Act
    with caplog.at_level(logging.ERROR, logger="app.modules.notifications.service"):
        DbNotifier(session_factory=_factory(db_session)).notify(
            [USER_A], "review_assigned", payload
        )

    # Assert
    assert db_session.query(Notification).count() == 0
    assert "review_assigned tidak dikirim" in caplog.text


def test_db_notifier_menelan_error_dan_tidak_melempar():
    # Arrange
    factory = MagicMock(side_effect=RuntimeError("db down"))

    # Act / Assert: tidak boleh melempar ke pemanggil
    DbNotifier(session_factory=factory).notify(
        [USER_A], "review_assigned", {"title": "t", "body": "b", "link": "/"}
    )
