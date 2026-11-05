"""Pemetaan baris query jadi SessionProgress. SCRUM-77 SCRUM-215."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.runs.adapters import SessionProgressAdapter

SESI = uuid.uuid4()
PRODUK = uuid.uuid4()
MULAI = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
DIPERBARUI = datetime(2026, 11, 3, 10, 5, tzinfo=UTC)
AKTIVITAS = datetime(2026, 11, 3, 10, 40, tzinfo=UTC)


def _baris(**ubah) -> SimpleNamespace:
    """Bentuk baris yang dikembalikan repository.session_progress."""
    nilai = {
        "product_id": PRODUK,
        "started_at": MULAI,
        "updated_at": DIPERBARUI,
        "total_calls": 9,
        "completed_calls": 4,
        "failed_calls": 1,
        "running_cost_idr": Decimal("4000.00"),
        "last_attempt_at": None,
    }
    nilai.update(ubah)
    return SimpleNamespace(**nilai)


def test_memetakan_baris_query_jadi_angka_progres():
    # Arrange
    db = MagicMock()
    with patch(
        "app.modules.runs.adapters.repository.session_progress",
        return_value=_baris(),
    ) as query:
        # Act
        hasil = SessionProgressAdapter(db).progress(SESI)

    # Assert
    query.assert_called_once_with(db, SESI)
    assert hasil is not None
    assert hasil.product_id == PRODUK
    assert hasil.total_calls == 9
    assert hasil.completed_calls == 4
    assert hasil.failed_calls == 1
    assert hasil.started_at == MULAI
    assert hasil.updated_at == DIPERBARUI
    assert hasil.running_cost_idr == Decimal("4000.00")


def test_sesi_yang_tidak_ada_jadi_none():
    # Arrange
    db = MagicMock()
    with patch(
        "app.modules.runs.adapters.repository.session_progress",
        return_value=None,
    ):
        # Act
        hasil = SessionProgressAdapter(db).progress(SESI)

    # Assert
    assert hasil is None

def test_waktu_terakhir_diperbarui_memakai_attempt_terbaru():
    # Arrange: angkanya berubah karena attempt, bukan karena baris sesi diperbarui
    db = MagicMock()
    with patch(
        "app.modules.runs.adapters.repository.session_progress",
        return_value=_baris(last_attempt_at=AKTIVITAS),
    ):
        # Act
        hasil = SessionProgressAdapter(db).progress(SESI)

    # Assert
    assert hasil is not None
    assert hasil.updated_at == AKTIVITAS


def test_sesi_tanpa_attempt_memakai_waktu_baris_sesinya():
    # Arrange: sesi mengantre, belum ada panggilan sama sekali
    db = MagicMock()
    with patch(
        "app.modules.runs.adapters.repository.session_progress",
        return_value=_baris(last_attempt_at=None),
    ):
        # Act
        hasil = SessionProgressAdapter(db).progress(SESI)

    # Assert
    assert hasil is not None
    assert hasil.updated_at == DIPERBARUI


def test_waktu_tanpa_zona_dari_database_dipasangi_utc():
    # Arrange: SQLite mengembalikan waktu tanpa zona, PostgreSQL dengan zona
    polos = datetime(2026, 11, 3, 10, 0)
    db = MagicMock()
    with patch(
        "app.modules.runs.adapters.repository.session_progress",
        return_value=_baris(started_at=polos, last_attempt_at=polos),
    ):
        # Act
        hasil = SessionProgressAdapter(db).progress(SESI)

    # Assert
    assert hasil is not None
    assert hasil.started_at == datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    assert hasil.updated_at == datetime(2026, 11, 3, 10, 0, tzinfo=UTC)