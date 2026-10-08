"""Test catatan audit tidak bisa diubah atau dihapus (SQLite).

PBI-18 AC2, lapisan trigger di diagram D6c. Trigger SQLite ini meniru
trigger PostgreSQL di migration, supaya test biasa ikut membuktikan
aturannya. Bukti di PostgreSQL sungguhan ada di
tests/integration/test_audit_immutable_pg.py.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditEntityType, AuditLog


@pytest.fixture
def baris(db_session) -> AuditLog:
    row = AuditLog(
        action="suite.created",
        entity_type=AuditEntityType.SUITE,
        entity_id=uuid.uuid4(),
        after={"name": "Asli"},
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_ubah_lewat_orm_ditolak(db_session, baris):
    # Arrange
    baris.after = {"name": "Dipalsukan"}

    # Act
    with pytest.raises(IntegrityError, match="append-only"):
        db_session.commit()
    db_session.rollback()

    # Assert
    db_session.expire_all()
    assert db_session.get(AuditLog, baris.id).after == {"name": "Asli"}


def test_hapus_lewat_orm_ditolak(db_session, baris):
    # Arrange
    db_session.delete(baris)

    # Act
    with pytest.raises(IntegrityError, match="append-only"):
        db_session.commit()
    db_session.rollback()

    # Assert
    assert db_session.query(AuditLog).count() == 1


@pytest.mark.parametrize(
    "perintah",
    [
        "UPDATE audit_logs SET action = 'suite.updated'",
        "UPDATE audit_logs SET actor_user_id = NULL",
        "DELETE FROM audit_logs",
    ],
)
def test_sql_mentah_juga_ditolak(db_session, baris, perintah):
    """Lapisan database berlaku walau aplikasi dilewati sama sekali."""
    # Act
    with pytest.raises(IntegrityError, match="append-only"):
        db_session.execute(text(perintah))
    db_session.rollback()

    # Assert
    db_session.expire_all()
    tersimpan = db_session.query(AuditLog).one()
    assert tersimpan.action == "suite.created"


def test_menambah_baris_tetap_boleh(db_session, baris):
    # Act
    db_session.add(
        AuditLog(action="suite.updated", entity_type=AuditEntityType.SUITE, entity_id=uuid.uuid4())
    )
    db_session.commit()

    # Assert
    assert [row.action for row in db_session.query(AuditLog).order_by(AuditLog.id)] == [
        "suite.created",
        "suite.updated",
    ]
