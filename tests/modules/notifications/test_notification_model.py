"""Model dan migrasi tabel notifications (SCRUM-153).

PBI-19: baris notifikasi punya user_id, type, title, body, link,
read_at, created_at. FK ke users.id.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.shared.security import Role

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000015301")


def test_notification_bisa_disimpan_dengan_kolom_wajib(db_session):
    # Arrange
    db_session.add(
        User(id=USER_ID, email="n@veritask.test", name="N", role=Role.AUTHOR, is_active=True)
    )
    db_session.commit()
    sekarang = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    # Act
    row = Notification(
        user_id=USER_ID,
        type="review_assigned",
        title="Tugas review baru",
        body="Anda ditugaskan mereview sebuah kasus.",
        link=f"/cases/{uuid.uuid4()}",
        read_at=None,
        created_at=sekarang,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    # Assert
    assert row.id is not None
    assert row.user_id == USER_ID
    assert row.type == "review_assigned"
    assert row.title == "Tugas review baru"
    assert row.body.startswith("Anda ditugaskan")
    assert row.link.startswith("/cases/")
    assert row.read_at is None
    assert row.created_at == sekarang


def test_tabel_notifications_punya_indeks_list_dan_unread():
    # Arrange / Act
    indexes = {idx.name for idx in Notification.__table__.indexes}
    columns = {col.name for col in Notification.__table__.columns}

    # Assert
    assert columns >= {
        "id",
        "user_id",
        "type",
        "title",
        "body",
        "link",
        "read_at",
        "created_at",
    }
    assert "ix_notifications_user_id_created_at" in indexes
    assert "ix_notifications_user_id_read_at" in indexes


def test_migrasi_notifications_mengikuti_head_saat_ini():
    # Arrange
    versions = Path("migrations/versions")
    files = list(versions.glob("*_create_notifications_table.py"))

    # Act / Assert
    assert len(files) == 1
    teks = files[0].read_text()
    assert 'down_revision: str | Sequence[str] | None = "b7e2d4f81c36"' in teks
    assert "notifications" in teks
    assert "fk_notifications_user_id" in teks
