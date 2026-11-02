"""GET /measurements/{id}/progress. SCRUM-77 SCRUM-215.

PBI-12 AC5 and AC6 over HTTP: Admin and Developer AI may poll it, other
roles may not, and an unknown measurement is a 404. The session tables do
not exist yet (SCRUM-205), so the happy path overrides the service.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.main import app
from app.modules.runs import dependencies
from app.modules.runs.progress_ports import SessionProgress
from app.modules.runs.service import ProgressService
from app.modules.runs.summary_ports import ProductInfo
from app.shared.security import Role

_SESI = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PRODUK = uuid.UUID("22222222-2222-2222-2222-222222222222")
_MULAI = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
_SEKARANG = _MULAI + timedelta(minutes=40)
URL = f"/measurements/{_SESI}/progress"


class _SessionsFake:
    def progress(self, measurement_id: uuid.UUID) -> SessionProgress | None:
        return SessionProgress(
            product_id=_PRODUK,
            total_calls=300,
            completed_calls=240,
            failed_calls=12,
            started_at=_MULAI,
            updated_at=_SEKARANG,
            running_cost_idr=Decimal("120000.00"),
        )


class _ProductsFake:
    def get(self, product_id: uuid.UUID) -> ProductInfo | None:
        return ProductInfo(
            id=_PRODUK,
            name="DeepSeek",
            measurement_type="automatic",
            is_active=True,
            provider_type="openai_compatible",
            model_name="deepseek-v4-flash",
            monthly_budget_idr=Decimal("500000.00"),
            price_output_usd_per_mtok=Decimal("0.42"),
        )


class _SpendFake:
    def spent_this_month_idr(self, product_id: uuid.UUID) -> Decimal:
        return Decimal("120000.00")


@pytest.fixture
def service_palsu():
    """Port sesi masih kosong sampai SCRUM-205, jadi jalur suksesnya di-override."""
    app.dependency_overrides[dependencies.get_progress_service] = lambda: ProgressService(
        sessions=_SessionsFake(),
        products=_ProductsFake(),
        spend=_SpendFake(),
    )
    yield
    app.dependency_overrides.pop(dependencies.get_progress_service, None)


def test_admin_membaca_angka_progres(as_role, service_palsu):
    # Arrange
    client = as_role(Role.ADMIN)

    # Act
    respons = client.get(URL)

    # Assert
    assert respons.status_code == 200
    isi = respons.json()
    assert isi["completed_calls"] == 240
    assert isi["total_calls"] == 300
    assert Decimal(str(isi["percent_complete"])) == Decimal("80.0")
    assert isi["failed_calls"] == 12
    assert Decimal(str(isi["percent_failed"])) == Decimal("5.0")
    assert isi["remaining_seconds"] == 600
    assert Decimal(str(isi["remaining_budget_idr"])) == Decimal("380000.00")


def test_developer_ai_boleh_memantau(as_role, service_palsu):
    # Arrange
    client = as_role(Role.DEVELOPER_AI)

    # Act + Assert
    assert client.get(URL).status_code == 200


@pytest.mark.parametrize("peran", [Role.CONTRIBUTOR, Role.VIEWER])
def test_peran_lain_ditolak(as_role, peran):
    # Arrange
    client = as_role(peran)

    # Act + Assert
    assert client.get(URL).status_code == 403


def test_pengukuran_tidak_dikenal_jadi_404(as_role):
    # Arrange: tanpa override, adapter asli belum punya tabel sesi
    client = as_role(Role.ADMIN)

    # Act
    respons = client.get(URL)

    # Assert
    assert respons.status_code == 404
    assert respons.json()["code"] == "MEASUREMENT_NOT_FOUND"
