"""GET /measurements/{id} membaca ulang tanda kendali sesi. PBI-12 AC8, AC9, SCRUM-214.

Blocker Roben di #97: PATCH accept dan jeda karena anggaran tersimpan di database,
tetapi detail sesi tidak mengembalikannya. Setelah muat ulang, atau saat Admin lain
membuka sesi, FE tidak bisa memulihkan tanda terima apa adanya maupun membedakan
jeda pengguna dari berhenti karena anggaran atau restart.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: permintaan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import SessionStatus, StopReason
from app.modules.runs.repository import SessionStatusRepository
from app.shared.security import Role
from tests.modules.conftest import USER_ID_QA
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _sesi

BERHENTI = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
_KOLOM_KENDALI = (
    "stopped_at",
    "stop_reason",
    "accepted_as_is_at",
    "accepted_as_is_by",
    "accepted_as_is_reason",
)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-kendali@veritask.test", name="Admin Kendali", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Kendali")
    db_session.add(item)
    db_session.commit()
    return item


def _utc(nilai: str) -> datetime:
    # SQLite membuang zona waktu; PostgreSQL mengembalikan waktu ber-zona (UTC).
    waktu = datetime.fromisoformat(nilai)
    return waktu if waktu.tzinfo else waktu.replace(tzinfo=UTC)


def _detail(client, session_id: uuid.UUID) -> dict:
    response = client.get(f"/measurements/{session_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_sesi_baru_punya_semua_kolom_kendali_kosong(as_role, db_session, produk, pembuat):
    """Edge: kolom selalu ada di respons, bernilai null sebelum ada kendali apa pun."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat)

    # Act
    body = _detail(as_role(Role.ADMIN), sesi.id)

    # Assert
    for kolom in _KOLOM_KENDALI:
        assert kolom in body
        assert body[kolom] is None


def test_terima_apa_adanya_terbaca_ulang_lewat_get(as_role, db_session, produk, pembuat):
    """Positif: alasan, pelaku, dan waktu dari PATCH muncul lagi di GET detail."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    client = as_role(Role.ADMIN)
    terima = client.patch(f"/measurements/{sesi.id}/accept", json={"reason": "Gangguan penyedia"})
    assert terima.status_code == 200

    # Act
    body = _detail(client, sesi.id)

    # Assert
    assert body["accepted_as_is_reason"] == "Gangguan penyedia"
    assert body["accepted_as_is_by"] == str(USER_ID_QA)
    assert body["accepted_as_is_at"] == terima.json()["accepted_as_is_at"]


def test_tanda_terima_yang_dihapus_terbaca_kosong(as_role, db_session, produk, pembuat):
    """Positif: setelah DELETE accept, GET tidak lagi menunjukkan tanda terima."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.FINISHED_WITH_FAILURES)
    client = as_role(Role.ADMIN)
    client.patch(f"/measurements/{sesi.id}/accept", json={"reason": "Sementara"})
    assert client.delete(f"/measurements/{sesi.id}/accept").status_code == 200

    # Act
    body = _detail(client, sesi.id)

    # Assert
    assert body["accepted_as_is_at"] is None
    assert body["accepted_as_is_by"] is None
    assert body["accepted_as_is_reason"] is None


@pytest.mark.parametrize("penyebab", [StopReason.USER, StopReason.BUDGET, StopReason.RESTART])
def test_penyebab_jeda_terbaca_ulang(as_role, db_session, produk, pembuat, penyebab):
    """Positif: jeda pengguna, anggaran, dan restart dibedakan lewat stop_reason di GET."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    SessionStatusRepository(db_session).change_status(
        sesi.id, SessionStatus.PAUSED, at=BERHENTI, stop_reason=penyebab
    )
    db_session.commit()

    # Act
    body = _detail(as_role(Role.ADMIN), sesi.id)

    # Assert
    assert body["status"] == "paused"
    assert body["stop_reason"] == penyebab.value
    assert _utc(body["stopped_at"]) == BERHENTI


def test_dilanjutkan_menghapus_penyebab_berhenti(as_role, db_session, produk, pembuat):
    """Edge: paused -> running mengosongkan stopped_at dan stop_reason di GET."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    status = SessionStatusRepository(db_session)
    status.change_status(sesi.id, SessionStatus.PAUSED, at=BERHENTI, stop_reason=StopReason.BUDGET)
    status.change_status(sesi.id, SessionStatus.RUNNING, at=BERHENTI)
    db_session.commit()

    # Act
    body = _detail(as_role(Role.ADMIN), sesi.id)

    # Assert
    assert body["status"] == "running"
    assert body["stopped_at"] is None
    assert body["stop_reason"] is None


def test_developer_ai_juga_membaca_kolom_kendali(as_role, db_session, produk, pembuat):
    """Positif: Dev AI memantau sesi (AC6), jadi penyebab jeda ikut terlihat; tidak ada isi kasus."""
    # Arrange
    sesi = _sesi(db_session, produk, pembuat, status=SessionStatus.RUNNING)
    SessionStatusRepository(db_session).change_status(
        sesi.id, SessionStatus.PAUSED, at=BERHENTI, stop_reason=StopReason.BUDGET
    )
    db_session.commit()

    # Act
    body = _detail(as_role(Role.DEVELOPER_AI), sesi.id)

    # Assert
    assert body["stop_reason"] == "budget"
