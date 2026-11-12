"""Harga dan anggaran produk penilai tanpa membuka kredensial. PBI-14 AC6, SCRUM-78 SCRUM-286.

Perkiraan biaya hanya butuh harga token dan batas bulanan, jadi kredensial terenkripsi
tidak pernah didekripsi. Produk yang dipakai sama dengan build_judge: hanya produk AI otomatis.

Label: Positif = harga dan batas terbaca, Negatif = bukan penilai yang sah,
Edge = harga kosong dan produk nonaktif.
"""

import uuid
from decimal import Decimal

import pytest

from app.modules.providers import judge_service
from app.shared.exceptions import NotFoundError
from tests.modules.providers.test_product_lists import _manual, _otomatis


def _simpan(db_session, produk):
    db_session.add(produk)
    db_session.commit()
    return produk


def test_harga_dan_batas_bulanan_produk_otomatis_terbaca(db_session):
    """Positif: harga masuk, harga keluar, batas bulanan, dan jenis penyedia diteruskan."""
    # Arrange
    produk = _simpan(
        db_session,
        _otomatis(
            "Penilai Harga",
            price_input_usd_per_mtok=Decimal("0.5"),
            price_output_usd_per_mtok=Decimal("1.5"),
            monthly_budget_idr=Decimal("250000.00"),
        ),
    )

    # Act
    harga = judge_service.judge_pricing(db_session, produk.id)

    # Assert
    assert harga.judge_provider_id == produk.id
    assert harga.provider_type == "openai_compatible"
    assert harga.price_input_usd_per_mtok == Decimal("0.5")
    assert harga.price_output_usd_per_mtok == Decimal("1.5")
    assert harga.monthly_budget_idr == Decimal("250000.00")


def test_kredensial_tidak_dibuka_saat_membaca_harga(db_session, monkeypatch):
    """Positif: tidak ada dekripsi, jadi kredensial rusak pun tidak menggagalkan estimasi."""
    # Arrange
    produk = _simpan(db_session, _otomatis("Penilai Tanpa Dekripsi"))

    def _dilarang(*_args, **_kwargs):
        raise AssertionError("kredensial tidak boleh didekripsi untuk perkiraan biaya")

    monkeypatch.setattr(judge_service, "decrypt_credential", _dilarang)

    # Act
    harga = judge_service.judge_pricing(db_session, produk.id)

    # Assert
    assert harga.judge_provider_id == produk.id


def test_harga_kosong_dikembalikan_apa_adanya(db_session):
    """Edge: penolakan harga kosong urusan open_judge_meter, bukan pembaca harga."""
    # Arrange
    produk = _simpan(
        db_session,
        _otomatis("Penilai Tanpa Harga", price_input_usd_per_mtok=None),
    )

    # Act
    harga = judge_service.judge_pricing(db_session, produk.id)

    # Assert
    assert harga.price_input_usd_per_mtok is None


def test_produk_nonaktif_tetap_terbaca_seperti_build_judge(db_session):
    """Edge: aktif atau tidaknya dicek pemilihan judge, bukan di sini."""
    # Arrange
    produk = _simpan(db_session, _otomatis("Penilai Nonaktif", is_active=False))

    # Act
    harga = judge_service.judge_pricing(db_session, produk.id)

    # Assert
    assert harga.judge_provider_id == produk.id


@pytest.mark.parametrize("jenis", ["manual", "tidak_ada"])
def test_bukan_produk_otomatis_ditolak_404(db_session, jenis):
    """Negatif: produk manual atau id yang tidak ada bukan penilai yang sah."""
    # Arrange
    if jenis == "manual":
        judge_id = _simpan(db_session, _manual("Penilai Manual")).id
    else:
        judge_id = uuid.uuid4()

    # Act
    with pytest.raises(NotFoundError) as galat:
        judge_service.judge_pricing(db_session, judge_id)

    # Assert
    assert galat.value.status_code == 404
    assert galat.value.code == "JUDGE_PROVIDER_NOT_FOUND"
