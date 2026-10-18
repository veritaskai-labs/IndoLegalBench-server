"""HTTP API notifikasi (SCRUM-153).

Ditambah berpasangan dengan implementasi: list dulu, lalu unread-count,
mark-read, dan read-all.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA

USER_LAIN = uuid.UUID("00000000-0000-0000-0000-00000000b001")
AWAL = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def pengguna_di_db(db_session):
    for user_id, email, name in (
        (USER_ID_QA, "qa@veritask.test", "Pengguna QA"),
        (USER_LAIN, "lain@veritask.test", "Pengguna Lain"),
    ):
        db_session.add(
            User(
                id=user_id,
                email=email,
                name=name,
                role=Role.AUTHOR,
                is_active=True,
            )
        )
    db_session.commit()


def _buat(
    db_session,
    *,
    user_id: uuid.UUID = USER_ID_QA,
    type: str = "review_assigned",
    title: str = "Judul",
    body: str = "Isi",
    link: str = "/cases/1",
    read_at: datetime | None = None,
    created_at: datetime | None = None,
) -> Notification:
    row = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        link=link,
        read_at=read_at,
        created_at=created_at or AWAL,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_daftar_kosong(as_role, pengguna_di_db):
    # Arrange
    client = as_role(Role.AUTHOR)

    # Act
    response = client.get("/notifications")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_daftar_dan_filter_unread(as_role, db_session, pengguna_di_db):
    # Arrange
    _buat(db_session, title="Belum", created_at=AWAL)
    _buat(
        db_session,
        title="Sudah",
        read_at=AWAL + timedelta(hours=1),
        created_at=AWAL + timedelta(minutes=1),
        type="review_decided",
    )
    _buat(db_session, user_id=USER_LAIN, title="Milik orang lain")
    client = as_role(Role.AUTHOR)

    # Act
    semua = client.get("/notifications").json()
    belum = client.get("/notifications", params={"unread": True}).json()

    # Assert
    assert semua["total"] == 2
    assert [item["title"] for item in semua["items"]] == ["Sudah", "Belum"]
    assert belum["total"] == 1
    assert belum["items"][0]["title"] == "Belum"
    assert belum["items"][0]["read_at"] is None


def test_tanpa_sesi_401(client, pengguna_di_db):
    # Arrange / Act / Assert
    assert client.get("/notifications").status_code == 401
