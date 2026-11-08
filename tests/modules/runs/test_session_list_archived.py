"""Daftar sesi arsip: service dan repository. PBI-11 AC10, SCRUM-263.

Service diuji dengan store dan NameLookup palsu (Mock), repository dengan SQLite.
Daftar arsip memakai kolom yang sama dengan daftar utama lewat to_list_item.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import SessionStatus, SessionType
from app.modules.runs.repository import SessionListRepository
from app.modules.runs.session_list import SessionListService, SessionRow
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

AWAL = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _row(**ubah) -> SessionRow:
    isi = {
        "id": uuid.uuid4(),
        "label": "Run lama",
        "created_at": AWAL,
        "created_by": uuid.uuid4(),
        "product_id": uuid.uuid4(),
        "repetitions": 2,
        "status": SessionStatus.FINISHED,
        "case_count": 4,
        "attempt_counts": {"succeeded": 6, "technical_failure": 2},
        "session_type": SessionType.AUTOMATIC,
        "completed_cases": 3,
        "total_cases": 4,
    }
    isi.update(ubah)
    return SessionRow(**isi)


def _service(row: SessionRow) -> tuple[SessionListService, Mock]:
    store = Mock()
    store.archived_sessions.return_value = [row]
    store.active_sessions.return_value = []
    names = Mock()
    names.product_names.return_value = {row.product_id: "Produk A"}
    names.user_names.return_value = {row.created_by: "Admin Sesi"}
    return SessionListService(sessions=store, names=names), store


def test_daftar_arsip_membaca_sesi_arsip_dengan_kolom_daftar_utama():
    """Positif: kolom sama dengan daftar utama, sumbernya store arsip."""
    # Arrange
    row = _row()
    service, store = _service(row)

    # Act
    (item,) = service.list(archived=True)

    # Assert
    store.archived_sessions.assert_called_once_with()
    store.active_sessions.assert_not_called()
    assert item.id == row.id
    assert item.label == "Run lama"
    assert item.product_name == "Produk A"
    assert item.created_by_name == "Admin Sesi"
    assert item.case_count == 4
    assert item.repetitions == 2
    assert item.status == SessionStatus.FINISHED
    assert (item.progress.completed, item.progress.total) == (8, 8)
    assert (item.progress.completed_cases, item.progress.total_cases) == (3, 4)
    assert item.progress.percent_cases_complete == 75


def test_daftar_default_tetap_membaca_sesi_aktif():
    """Negatif: tanpa filter arsip, store arsip tidak disentuh (AC10)."""
    # Arrange
    service, store = _service(_row())

    # Act
    result = service.list()

    # Assert
    assert result == []
    store.active_sessions.assert_called_once_with()
    store.archived_sessions.assert_not_called()


def test_daftar_arsip_kosong_mengembalikan_list_kosong():
    """Edge: tanpa sesi arsip hasilnya list kosong."""
    # Arrange
    service, store = _service(_row())
    store.archived_sessions.return_value = []

    # Act
    result = service.list(archived=True)

    # Assert
    assert result == []


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-263@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk 263")
    db_session.add(item)
    db_session.commit()
    return item


def test_repository_arsip_hanya_memuat_sesi_arsip_terbaru_diarsipkan_di_atas(
    db_session, produk, pembuat
):
    """Positif: sesi aktif tidak ikut, urutan menurut waktu arsip terbaru."""
    # Arrange
    _sesi(db_session, produk, pembuat, label="aktif", status=SessionStatus.FINISHED)
    lama = _sesi(
        db_session,
        produk,
        pembuat,
        label="arsip lama",
        status=SessionStatus.FINISHED,
        archived_at=AWAL,
        archived_by=pembuat.id,
    )
    baru = _sesi(
        db_session,
        produk,
        pembuat,
        label="arsip baru",
        status=SessionStatus.FINISHED,
        archived_at=AWAL + timedelta(hours=1),
        archived_by=pembuat.id,
    )

    # Act
    rows = SessionListRepository(db_session).archived_sessions()

    # Assert
    assert [row.id for row in rows] == [baru.id, lama.id]
    assert all(row.label.startswith("arsip") for row in rows)


def test_repository_aktif_tidak_memuat_sesi_arsip(db_session, produk, pembuat):
    """Edge: kedua daftar saling lepas, sesi arsip tidak bocor ke daftar utama."""
    # Arrange
    _sesi(
        db_session,
        produk,
        pembuat,
        status=SessionStatus.FINISHED,
        archived_at=AWAL,
        archived_by=pembuat.id,
    )

    # Act
    rows = SessionListRepository(db_session).active_sessions()

    # Assert
    assert rows == []
