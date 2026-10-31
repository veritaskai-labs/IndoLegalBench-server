"""Real ports for the summary, built on other modules' services. SCRUM-76 SCRUM-206.

The runs module reaches cases, suites and providers only through their
service functions. Those services are mocked here, so no test touches a
database. Spending and answer length history have no table yet (SCRUM-209),
so their ports say plainly that there is nothing.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.modules.cases.models import SplitTag
from app.modules.providers.models import MeasurementType, ProviderType
from app.modules.providers.schemas import AiProductRead
from app.modules.runs import adapters
from app.shared.exceptions import NotFoundError

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


def _produk_read(**ubah) -> AiProductRead:
    nilai = {
        "id": PRODUCT_ID,
        "name": "AiYU",
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://api.example.test/v1/chat/completions",
        "model_name": "deepseek-flash",
        "credential_hint": "9xQ2",
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("500000.00"),
        "is_active": True,
        "last_test_at": None,
        "last_test_status": None,
        "last_test_message": None,
        "last_test_error_category": None,
        "created_by": uuid.uuid4(),
        "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    nilai.update(ubah)
    return AiProductRead(**nilai)


def test_kasus_memeriksa_suite_lalu_menghitung_dan_memberi_kunci_string():
    # Arrange
    db = MagicMock(name="db")
    with (
        patch.object(adapters, "suites_service") as suites,
        patch.object(adapters, "cases_service") as cases,
    ):
        cases.count_approved_by_split.return_value = {SplitTag.DEV: 2, SplitTag.TEST: 1}

        # Act
        hasil = adapters.CaseCountAdapter(db).approved_counts({SUITE_A, SUITE_B})

    # Assert
    suites.require_existing.assert_called_once_with(db, {SUITE_A, SUITE_B})
    cases.count_approved_by_split.assert_called_once_with(db, {SUITE_A, SUITE_B})
    assert hasil == {"dev": 2, "test": 1}
    assert all(type(kunci) is str for kunci in hasil)


def test_suite_tidak_ada_menghentikan_sebelum_menghitung_kasus():
    # Arrange
    db = MagicMock(name="db")
    with (
        patch.object(adapters, "suites_service") as suites,
        patch.object(adapters, "cases_service") as cases,
    ):
        suites.require_existing.side_effect = NotFoundError("Suite tidak ditemukan")

        # Act
        with pytest.raises(NotFoundError):
            adapters.CaseCountAdapter(db).approved_counts({SUITE_A})

    # Assert
    cases.count_approved_by_split.assert_not_called()


def test_produk_dipetakan_ke_info_tanpa_kredensial():
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read(
            price_output_usd_per_mtok=Decimal("0.4200")
        )

        # Act
        info = adapters.ProductAdapter(db).get(PRODUCT_ID)

    # Assert
    providers.get_product.assert_called_once_with(db, PRODUCT_ID)
    assert info.id == PRODUCT_ID
    assert info.name == "AiYU"
    assert info.measurement_type == "automatic"
    assert info.is_active is True
    assert info.provider_type == "openai_compatible"
    assert type(info.provider_type) is str
    assert info.model_name == "deepseek-flash"
    assert info.monthly_budget_idr == Decimal("500000.00")
    assert info.price_output_usd_per_mtok == Decimal("0.4200")
    assert not hasattr(info, "base_url")
    assert not hasattr(info, "credential_hint")


def test_produk_manual_dipetakan_dengan_tipe_asli_dan_tanpa_provider():
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read(
            measurement_type=MeasurementType.MANUAL,
            provider_type=None,
            model_name=None,
            base_url=None,
        )

        # Act
        info = adapters.ProductAdapter(db).get(PRODUCT_ID)

    # Assert
    assert info.measurement_type == "manual"
    assert type(info.measurement_type) is str
    assert info.provider_type is None
    assert info.model_name is None


def test_produk_otomatis_tanpa_harga_menjadi_none_agar_katalog_dipakai():
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read(price_output_usd_per_mtok=None)

        # Act
        info = adapters.ProductAdapter(db).get(PRODUCT_ID)

    # Assert
    assert info.measurement_type == "automatic"
    assert info.price_output_usd_per_mtok is None


def test_produk_nonaktif_tetap_dikembalikan_agar_service_yang_menolak():
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read(is_active=False)

        # Act
        info = adapters.ProductAdapter(db).get(PRODUCT_ID)

    # Assert
    assert info.is_active is False


def test_produk_tidak_ada_atau_terhapus_menjadi_none():
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.side_effect = NotFoundError("Produk AI tidak ditemukan")

        # Act
        info = adapters.ProductAdapter(db).get(PRODUCT_ID)

    # Assert
    assert info is None


def test_pengeluaran_belum_tersedia_dilaporkan_nol():
    # Arrange
    port = adapters.PendingSpend()

    # Act
    terpakai = port.spent_this_month_idr(PRODUCT_ID)

    # Assert
    assert terpakai == Decimal("0.00")


def test_riwayat_panjang_jawaban_belum_tersedia_dilaporkan_none():
    # Arrange
    port = adapters.PendingAnswerHistory()

    # Act
    rata_rata = port.average_answer_tokens(PRODUCT_ID)

    # Assert
    assert rata_rata is None
