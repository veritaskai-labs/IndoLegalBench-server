"""Percobaan manual yang tidak ada di ManualSessionRepository. SCRUM-77 SCRUM-267.

SCRUM-267 menyentuh repository.py; dua penjaga ini (dari SCRUM-231) belum diuji,
jadi ditutup di sini supaya file yang diubah tetap 100%. Database uji SQLite.

Label: Negatif = id percobaan tidak ada.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.modules.runs.models import AnswerHistory
from app.modules.runs.repository import ManualSessionRepository
from app.shared.exceptions import NotFoundError

JAM = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)
PENGGUNA = uuid.UUID("00000000-0000-0000-0000-0000000000d4")


def test_arsip_jawaban_percobaan_yang_tidak_ada_tidak_menulis_apa_pun(db_session):
    """Negatif: tidak ada baris riwayat untuk percobaan yang tidak ada."""
    # Act
    ManualSessionRepository(db_session).archive_answer(
        uuid.uuid4(), overwritten_by=PENGGUNA, overwritten_at=JAM
    )
    db_session.flush()

    # Assert
    assert db_session.scalar(select(func.count()).select_from(AnswerHistory)) == 0


def test_tulis_jawaban_percobaan_yang_tidak_ada_ditolak(db_session):
    """Negatif: 404 MANUAL_ATTEMPT_NOT_FOUND, bukan galat atribut."""
    # Act
    with pytest.raises(NotFoundError) as galat:
        ManualSessionRepository(db_session).write_answer(
            uuid.uuid4(),
            answer_md="jawaban",
            platform_refused=False,
            note=None,
            obtained_at=JAM,
            filled_by=PENGGUNA,
            filled_at=JAM,
        )

    # Assert
    assert galat.value.code == "MANUAL_ATTEMPT_NOT_FOUND"
