"""latest_run di GET /dashboard: sesi pengukuran terakhir. PBI-19 AC1, PBI-11, SCRUM-210.

Isinya kolom yang sama dengan satu baris GET /measurements. Hanya Admin yang
mendapatkannya, karena data sesi hanya untuk Admin dan Developer AI, dan Developer
AI tidak punya dashboard. Sesi yang diarsipkan dilewati, sama seperti daftar sesi.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: data yang tidak boleh keluar.
- Edge: batas atau kasus pinggir.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.models import User
from app.modules.runs.models import AttemptStatus, SessionStatus
from app.shared.security import Role
from tests.modules.providers.test_product_lists import _otomatis
from tests.modules.runs.test_session_list_endpoint import _mulai, _sesi

AWAL = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def pembuat(db_session) -> User:
    user = User(email="pembuat-dashboard@veritask.test", name="Admin Sesi", role=Role.ADMIN)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def produk(db_session):
    item = _otomatis("Produk Dashboard")
    db_session.add(item)
    db_session.commit()
    return item


def test_admin_melihat_sesi_terakhir(as_role, db_session, produk, pembuat):
    """Positif: sesi terbaru dengan kolom daftar dan progresnya."""
    # Arrange
    _sesi(db_session, produk, pembuat, label="Lama", created_at=AWAL)
    baru = _sesi(
        db_session,
        produk,
        pembuat,
        label="Baru",
        created_at=AWAL + timedelta(hours=1),
        status=SessionStatus.RUNNING,
    )
    _mulai(db_session, baru, [AttemptStatus.SUCCEEDED, AttemptStatus.PENDING])

    # Act
    response = as_role(Role.ADMIN).get("/dashboard")

    # Assert
    assert response.status_code == 200
    terakhir = response.json()["latest_run"]
    assert terakhir["id"] == str(baru.id)
    assert terakhir["label"] == "Baru"
    assert terakhir["product_name"] == "Produk Dashboard"
    assert terakhir["status"] == "running"
    assert terakhir["progress"] == {
        "completed": 1,
        "total": 2,
        "completed_cases": 0,
        "total_cases": 1,
        "percent_cases_complete": 0,
    }


def test_belum_ada_sesi_bernilai_null(as_role):
    """Edge: sebelum pengukuran pertama, latest_run tetap null."""
    # Act
    body = as_role(Role.ADMIN).get("/dashboard").json()

    # Assert
    assert body["latest_run"] is None


def test_sesi_terbaru_yang_diarsipkan_dilewati(as_role, db_session, produk, pembuat):
    """Edge: yang tampil sesi terbaru yang belum diarsipkan, sama seperti daftar sesi."""
    # Arrange
    _sesi(db_session, produk, pembuat, label="Masih tampil", created_at=AWAL)
    _sesi(
        db_session,
        produk,
        pembuat,
        label="Diarsipkan",
        created_at=AWAL + timedelta(hours=1),
        status=SessionStatus.FINISHED,
        archived_at=AWAL + timedelta(hours=2),
        archived_by=pembuat.id,
    )

    # Act
    body = as_role(Role.ADMIN).get("/dashboard").json()

    # Assert
    assert body["latest_run"]["label"] == "Masih tampil"


def test_kontributor_tidak_mendapat_sesi_terakhir(as_role, db_session, produk, pembuat):
    """Negatif: Kontributor tidak membuka data sesi, jadi tetap null walau sesi ada."""
    # Arrange
    _sesi(db_session, produk, pembuat)

    # Act
    body = as_role(Role.CONTRIBUTOR).get("/dashboard").json()

    # Assert
    assert body["latest_run"] is None
