"""Sumber snapshot kasus dan salinan produk untuk sesi yang mulai berjalan. SCRUM-76 SCRUM-207.

PBI-11 AC2 dan AC6. Modul runs hanya menjangkau cases dan providers lewat
fungsi service mereka (aturan modular monolith), jadi service itu di-mock di
sini dan tidak ada test yang menyentuh database.

- Kasus: cases_service.approved_copies_for_suite memberi versi disetujui
  terbaru tiap kasus (kasus yang sedang diedit tetap memakai versi itu) dan
  meninggalkan kasus yang belum pernah disetujui.
- Produk: salinan sesuai D2 session_product_copies, tanpa kredensial
  ([USULAN SA] "kredensial TIDAK ikut disalin").

Label: Positif = data tersalin benar, Negatif = data yang sengaja tidak disalin.
"""

import dataclasses
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.modules.providers.models import MeasurementType, ProviderType
from app.modules.providers.schemas import AiProductRead, ModelParams
from app.modules.runs import adapters, session_ports
from app.shared.exceptions import ValidationError

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


def _salinan_kasus(kode: str, tag: str) -> dict:
    """Bentuk persis keluaran approved_copies_for_suite (cases/service.py)."""
    return {
        "case_id": uuid.uuid5(uuid.NAMESPACE_URL, kode),
        "case_version_id": uuid.uuid5(uuid.NAMESPACE_DNS, kode),
        "body": {
            "version_no": 2,
            "status": "approved",
            "sections": {"case_code": kode, "split_tag": tag, "question": f"Soal {kode}?"},
        },
    }


def test_kasus_disetujui_dari_setiap_suite_dibekukan_sesuai_urutan_suite():
    """Positif: AC2, semua kasus disetujui dari suite yang dipilih, suite pertama lebih dulu."""
    # Arrange
    db = MagicMock(name="db")
    per_suite = {
        SUITE_A: [_salinan_kasus("PHK-001", "dev"), _salinan_kasus("PHK-002", "test")],
        SUITE_B: [_salinan_kasus("KTR-001", "dev")],
    }
    with patch.object(adapters, "cases_service") as cases:
        cases.approved_copies_for_suite.side_effect = lambda _db, suite_id: per_suite[suite_id]

        # Act
        items = adapters.SnapshotSourceAdapter(db).approved_items((SUITE_A, SUITE_B))

    # Assert
    assert [item.content["sections"]["case_code"] for item in items] == [
        "PHK-001",
        "PHK-002",
        "KTR-001",
    ]
    assert [item.split_tag for item in items] == ["dev", "test", "dev"]


def test_item_snapshot_berisi_referensi_kasus_live_dan_salinan_persis():
    """Positif: AC6, case_id adalah id kasus live dan isi disalin persis dari versi disetujui."""
    # Arrange
    db = MagicMock(name="db")
    salinan = _salinan_kasus("PHK-001", "dev")
    with patch.object(adapters, "cases_service") as cases:
        cases.approved_copies_for_suite.return_value = [salinan]

        # Act
        (item,) = adapters.SnapshotSourceAdapter(db).approved_items((SUITE_A,))

    # Assert
    assert item == session_ports.SnapshotItem(
        case_id=salinan["case_id"],
        case_version_id=salinan["case_version_id"],
        split_tag="dev",
        content=salinan["body"],
    )


def _produk_read(**ubah) -> AiProductRead:
    nilai = {
        "id": PRODUCT_ID,
        "name": "AiYU",
        "measurement_type": MeasurementType.AUTOMATIC,
        "provider_type": ProviderType.OPENAI_COMPATIBLE,
        "base_url": "https://api.example.test/v1/chat/completions",
        "model_name": "deepseek-flash",
        "credential_hint": "9xQ2",
        "has_credential": True,
        "rate_limit_per_minute": 30,
        "monthly_budget_idr": Decimal("500000.00"),
        "model_params": ModelParams({"temperature": 0.3}),
        "price_input_usd_per_mtok": Decimal("1"),
        "price_output_usd_per_mtok": Decimal("10"),
        "is_active": True,
        "created_by": uuid.uuid4(),
        "created_at": datetime(2026, 10, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    nilai.update(ubah)
    return AiProductRead(**nilai)


def test_salinan_produk_memuat_kolom_d2_dengan_nilai_saat_ini():
    """Positif: AC6, perubahan produk setelah sesi berjalan tidak memengaruhi sesi itu."""
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read()

        # Act
        salinan = adapters.ProductCopyAdapter(db).copy(PRODUCT_ID)

    # Assert
    providers.get_product.assert_called_once_with(db, PRODUCT_ID)
    assert salinan == session_ports.ProductCopy(
        product_id=PRODUCT_ID,
        name="AiYU",
        measurement_type="automatic",
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1/chat/completions",
        model_name="deepseek-flash",
        # Sejak #93 (SCRUM-247) model_params objek JSON bebas: hanya kunci yang diisi Admin.
        model_params={"temperature": 0.3},
        rate_limit_per_minute=30,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("10"),
        monthly_budget_idr=Decimal("500000.00"),
    )


def test_salinan_produk_tidak_membawa_kredensial_maupun_petunjuknya():
    """Negatif: kredensial tidak ikut disalin (D2), petunjuk 4 karakternya juga tidak."""
    # Arrange + Act
    kolom = {field.name for field in dataclasses.fields(session_ports.ProductCopy)}

    # Assert
    assert not any("credential" in nama for nama in kolom)


def test_produk_tanpa_parameter_model_disalin_kosong():
    """Positif: model_params kosong tetap kosong, penyedia memakai bawaannya (PBI-11 AC4)."""
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read(model_params=None)

        # Act
        salinan = adapters.ProductCopyAdapter(db).copy(PRODUCT_ID)

    # Assert
    assert salinan.model_params is None


def test_produk_nonaktif_tidak_disalin():
    """Negatif: review #87, produk yang dinonaktifkan selama sesi menunggu tidak boleh dipakai.

    Kodenya sama dengan penolakan ringkasan SCRUM-206 (PRODUCT_INACTIVE).
    """
    # Arrange
    db = MagicMock(name="db")
    with patch.object(adapters, "providers_service") as providers:
        providers.get_product.return_value = _produk_read(is_active=False)

        # Act + Assert
        with pytest.raises(ValidationError) as galat:
            adapters.ProductCopyAdapter(db).copy(PRODUCT_ID)

    assert galat.value.code == "PRODUCT_INACTIVE"


def test_salinan_produk_tidak_membawa_batas_laju_yang_belum_punya_kolom():
    """Negatif: review #87, session_product_copies (#89) belum punya kolom rate_limit_per_minute.

    Nilai yang disalin lalu dibuang membuat salinan tampak beku padahal tidak
    tersimpan, jadi kolom itu tidak ikut sampai tabelnya punya tempat.
    """
    # Arrange + Act
    kolom = {field.name for field in dataclasses.fields(session_ports.ProductCopy)}

    # Assert
    assert "rate_limit_per_minute" not in kolom
