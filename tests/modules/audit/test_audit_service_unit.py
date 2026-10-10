"""Unit test audit.service.record() dengan dependency di-mock.

PBI-18 AC1. Repository dan request_context dipalsukan, jadi test ini
hanya membuktikan logika service: validasi, penyaringan rahasia, dan
pengisian pelaku. Bukti bahwa baris benar-benar tersimpan dalam satu
transaksi ada di test_audit_record.py (integrasi dengan SQLite).
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.modules.audit import service
from app.modules.audit.models import AuditEntityType, AuditLog
from app.shared.exceptions import ValidationError
from app.shared.request_context import Actor

ENTITY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
REQUEST_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d1")


@pytest.fixture
def repo():
    with patch.object(service, "repository") as palsu:
        palsu.add.side_effect = lambda _db, row: row
        yield palsu


@pytest.fixture
def konteks():
    with patch.object(service, "request_context") as palsu:
        palsu.actor.return_value = None
        palsu.request_id.return_value = None
        yield palsu


def _record(db, **kolom):
    nilai = {
        "action": "suite.updated",
        "entity_type": AuditEntityType.SUITE,
        "entity_id": ENTITY_ID,
    }
    nilai.update(kolom)
    return service.record(db, **nilai)


def test_baris_diserahkan_ke_repository_dengan_session_yang_sama(repo, konteks):
    # Arrange
    db = MagicMock()

    # Act
    row = _record(db, before={"name": "A"}, after={"name": "B"})

    # Assert
    repo.add.assert_called_once_with(db, row)
    assert isinstance(row, AuditLog)
    assert (row.action, row.entity_type, row.entity_id) == ("suite.updated", "suite", ENTITY_ID)
    assert (row.before, row.after) == ({"name": "A"}, {"name": "B"})


def test_service_tidak_pernah_commit(repo, konteks):
    """D6b: commit milik pemanggil, bukan pencatat audit."""
    # Arrange
    db = MagicMock()

    # Act
    _record(db)

    # Assert
    db.commit.assert_not_called()
    db.flush.assert_not_called()


def test_pelaku_dan_request_id_diambil_dari_konteks(repo, konteks):
    # Arrange
    db = MagicMock()
    konteks.actor.return_value = Actor(user_id=ACTOR_ID, role="admin")
    konteks.request_id.return_value = REQUEST_ID

    # Act
    row = _record(db)

    # Assert
    konteks.actor.assert_called_once_with(db)
    assert (row.actor_user_id, row.actor_role, row.request_id) == (ACTOR_ID, "admin", REQUEST_ID)


def test_tanpa_pelaku_dicatat_sebagai_sistem(repo, konteks):
    # Act
    row = _record(MagicMock())

    # Assert
    assert (row.actor_user_id, row.actor_role) == (None, None)


def test_nilai_rahasia_disaring_sebelum_disimpan(repo, konteks):
    # Act
    row = _record(MagicMock(), after={"name": "GPT", "api_key": "sk-x"})

    # Assert
    assert row.after == {"name": "GPT"}


def test_alasan_dirapikan(repo, konteks):
    # Act
    row = _record(MagicMock(), reason="  Reviewer A cuti  ")

    # Assert
    assert row.reason == "Reviewer A cuti"


@pytest.mark.parametrize("alasan", [None, "", "   "])
def test_ganti_reviewer_tanpa_alasan_ditolak_tanpa_menulis(repo, konteks, alasan):
    # Act
    with pytest.raises(ValidationError) as galat:
        _record(
            MagicMock(),
            action="review.reviewer_replaced",
            entity_type=AuditEntityType.REVIEW,
            reason=alasan,
        )

    # Assert
    assert galat.value.field == "reason"
    repo.add.assert_not_called()


@pytest.mark.parametrize("action", ["", "SuiteUpdated", "suite", "suite.Updated", "suite..x"])
def test_action_di_luar_konvensi_ditolak_tanpa_menulis(repo, konteks, action):
    # Act
    with pytest.raises(ValueError):
        _record(MagicMock(), action=action)

    # Assert
    repo.add.assert_not_called()


def test_jenis_objek_di_luar_katalog_ditolak_tanpa_menulis(repo, konteks):
    # Act
    with pytest.raises(ValueError):
        _record(MagicMock(), entity_type="session")

    # Assert
    repo.add.assert_not_called()
