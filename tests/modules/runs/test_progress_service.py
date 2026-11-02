"""Progress endpoint service. SCRUM-77 SCRUM-215.

PBI-12 AC5: the screen reads the numbers of one measurement in a single
light call. The session tables do not exist yet, so the service only
speaks through ports and these tests use fakes.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.runs.progress_ports import SessionProgress
from app.modules.runs.service import ProgressService
from app.modules.runs.summary_ports import ProductInfo
from app.shared.exceptions import InternalError, NotFoundError

_SESI = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PRODUK = uuid.UUID("22222222-2222-2222-2222-222222222222")
_MULAI = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
_SEKARANG = _MULAI + timedelta(minutes=40)


class _SessionsFake:
    def __init__(self, hitungan: SessionProgress | None) -> None:
        self._hitungan = hitungan

    def progress(self, measurement_id: uuid.UUID) -> SessionProgress | None:
        return self._hitungan


class _ProductsFake:
    def __init__(self, produk: ProductInfo | None) -> None:
        self._produk = produk

    def get(self, product_id: uuid.UUID) -> ProductInfo | None:
        return self._produk


class _SpendFake:
    def __init__(self, terpakai: Decimal) -> None:
        self._terpakai = terpakai

    def spent_this_month_idr(self, product_id: uuid.UUID) -> Decimal:
        return self._terpakai


def _hitungan(**ubah) -> SessionProgress:
    nilai = {
        "product_id": _PRODUK,
        "total_calls": 300,
        "completed_calls": 240,
        "failed_calls": 12,
        "started_at": _MULAI,
        "updated_at": _SEKARANG,
        "running_cost_idr": Decimal("120000.00"),
    }
    nilai.update(ubah)
    return SessionProgress(**nilai)


def _produk(**ubah) -> ProductInfo:
    nilai = {
        "id": _PRODUK,
        "name": "DeepSeek",
        "measurement_type": "automatic",
        "is_active": True,
        "provider_type": "openai_compatible",
        "model_name": "deepseek-v4-flash",
        "monthly_budget_idr": Decimal("500000.00"),
        "price_output_usd_per_mtok": Decimal("0.42"),
    }
    nilai.update(ubah)
    return ProductInfo(**nilai)


def _service(
    *,
    hitungan: SessionProgress | None,
    produk: ProductInfo | None,
    terpakai: Decimal = Decimal("120000.00"),
) -> ProgressService:
    return ProgressService(
        sessions=_SessionsFake(hitungan),
        products=_ProductsFake(produk),
        spend=_SpendFake(terpakai),
    )


def test_angka_progres_satu_sesi():
    # Arrange
    service = _service(hitungan=_hitungan(), produk=_produk())

    # Act
    respons = service.read(_SESI)

    # Assert
    assert respons.completed_calls == 240
    assert respons.total_calls == 300
    assert respons.percent_complete == Decimal("80.0")
    assert respons.failed_calls == 12
    assert respons.percent_failed == Decimal("5.0")
    assert respons.remaining_seconds == 600
    assert respons.running_cost_idr == Decimal("120000.00")
    assert respons.remaining_budget_idr == Decimal("380000.00")
    assert respons.started_at == _MULAI
    assert respons.updated_at == _SEKARANG


def test_respons_hanya_memuat_angka_yang_diminta_ac5():
    # Arrange: AC5 melarang jumlah coba ulang berjalan dan penanda produk bermasalah
    service = _service(hitungan=_hitungan(), produk=_produk())

    # Act
    isi = service.read(_SESI).model_dump()

    # Assert
    assert set(isi) == {
        "completed_calls",
        "total_calls",
        "percent_complete",
        "failed_calls",
        "percent_failed",
        "remaining_seconds",
        "running_cost_idr",
        "remaining_budget_idr",
        "started_at",
        "updated_at",
    }


def test_sesi_tidak_ada_jadi_tidak_ditemukan():
    # Arrange
    service = _service(hitungan=None, produk=_produk())

    # Act + Assert
    with pytest.raises(NotFoundError) as exc:
        service.read(_SESI)

    assert exc.value.code == "MEASUREMENT_NOT_FOUND"


def test_produk_tidak_ada_jadi_tidak_ditemukan():
    # Arrange
    service = _service(hitungan=_hitungan(), produk=None)

    # Act + Assert
    with pytest.raises(NotFoundError) as exc:
        service.read(_SESI)

    assert exc.value.code == "AI_PRODUCT_NOT_FOUND"


def test_anggaran_belum_disetel_jadi_error_internal():
    # Arrange
    service = _service(hitungan=_hitungan(), produk=_produk(monthly_budget_idr=None))

    # Act + Assert
    with pytest.raises(InternalError) as exc:
        service.read(_SESI)

    assert exc.value.code == "BUDGET_NOT_CONFIGURED"
