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