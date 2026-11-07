"""Service unarchive_session: kembalikan sesi dari arsip. PBI-11 AC10, SCRUM-263.

Repository dan sesi database diganti Mock dan SimpleNamespace, jadi tes ini tidak
menyentuh DB. Alur HTTP dan audit diuji di test_session_unarchive_endpoint.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.modules.runs import session_unarchive
from app.modules.runs.models import SessionStatus
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.shared.security import Role

PEMBUAT = uuid.UUID("00000000-0000-0000-0000-000000000263")
LAIN = uuid.UUID("00000000-0000-0000-0000-000000000999")
WAKTU_ARSIP = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _sesi(**ubah) -> SimpleNamespace:
    isi = {
        "id": uuid.uuid4(),
        "status": SessionStatus.FINISHED,
        "created_by": PEMBUAT,
        "archived_at": WAKTU_ARSIP,
        "archived_by": PEMBUAT,
    }
    isi.update(ubah)
    return SimpleNamespace(**isi)


@pytest.fixture
def db() -> Mock:
    return Mock()


@pytest.fixture
def baca() -> Mock:
    """Pengganti repository.get_session_for_update. Tiap tes mengisi return_value-nya."""
    with patch.object(session_unarchive.repository, "get_session_for_update") as pembaca:
        yield pembaca


def test_admin_mengembalikan_sesi_siapa_pun(baca, db):
    """Positif: archived_at dan archived_by dikosongkan, status tetap, lalu commit."""
    # Arrange
    sesi = _sesi(created_by=LAIN)
    baca.return_value = sesi

    # Act
    result = session_unarchive.unarchive_session(
        db, sesi.id, actor_id=PEMBUAT, actor_role=Role.ADMIN
    )

    # Assert
    assert sesi.archived_at is None
    assert sesi.archived_by is None
    assert sesi.status == SessionStatus.FINISHED
    assert result.id == sesi.id
    assert result.status == SessionStatus.FINISHED
    db.commit.assert_called_once_with()


def test_developer_ai_mengembalikan_sesi_miliknya(baca, db):
    """Positif: Developer AI boleh memulihkan sesi yang ia buat."""
    # Arrange
    sesi = _sesi(created_by=PEMBUAT)
    baca.return_value = sesi

    # Act
    session_unarchive.unarchive_session(db, sesi.id, actor_id=PEMBUAT, actor_role=Role.DEVELOPER_AI)

    # Assert
    assert sesi.archived_at is None
    assert sesi.archived_by is None


def test_developer_ai_tidak_boleh_mengembalikan_sesi_orang_lain(baca, db):
    """Negatif: 403 NOT_YOUR_SESSION dan sesinya tetap diarsipkan."""
    # Arrange
    sesi = _sesi(created_by=LAIN)
    baca.return_value = sesi

    # Act
    with pytest.raises(ForbiddenError) as error:
        session_unarchive.unarchive_session(
            db, sesi.id, actor_id=PEMBUAT, actor_role=Role.DEVELOPER_AI
        )

    # Assert
    assert error.value.code == "NOT_YOUR_SESSION"
    assert sesi.archived_at == WAKTU_ARSIP
    db.commit.assert_not_called()


def test_sesi_tidak_ada_ditolak_404(baca, db):
    """Negatif: id yang tidak dikenal menjadi MEASUREMENT_NOT_FOUND."""
    # Arrange
    baca.return_value = None

    # Act
    with pytest.raises(NotFoundError) as error:
        session_unarchive.unarchive_session(
            db, uuid.uuid4(), actor_id=PEMBUAT, actor_role=Role.ADMIN
        )

    # Assert
    assert error.value.code == "MEASUREMENT_NOT_FOUND"
    db.commit.assert_not_called()


def test_sesi_yang_belum_diarsipkan_ditolak_409(baca, db):
    """Negatif: unarchive kedua (atau pada sesi aktif) tidak mengubah apa pun."""
    # Arrange
    sesi = _sesi(archived_at=None, archived_by=None)
    baca.return_value = sesi

    # Act
    with pytest.raises(ConflictError) as error:
        session_unarchive.unarchive_session(db, sesi.id, actor_id=PEMBUAT, actor_role=Role.ADMIN)

    # Assert
    assert error.value.code == "SESSION_NOT_ARCHIVED"
    db.commit.assert_not_called()


def test_sesi_dikunci_saat_dibaca(baca, db):
    """Edge: baris dibaca lewat get_session_for_update supaya request ganda berurutan."""
    # Arrange
    sesi = _sesi()
    baca.return_value = sesi

    # Act
    session_unarchive.unarchive_session(db, sesi.id, actor_id=PEMBUAT, actor_role=Role.ADMIN)

    # Assert
    baca.assert_called_once_with(db, sesi.id)
