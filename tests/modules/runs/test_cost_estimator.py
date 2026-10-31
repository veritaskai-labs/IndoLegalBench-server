"""Cost estimate for one measurement. SCRUM-76 SCRUM-206.

PBI-11 AC5: average answer length of earlier measurements, times the
price, times the daily exchange rate, plus a safety margin. The estimate
is a rupiah amount rounded up, and it never uses float.
"""

from decimal import Decimal

import pytest

from app.modules.runs import cost_estimator
from app.shared.exceptions import InternalError


def _hitung(**ubah) -> Decimal:
    nilai = {
        "total_calls": 60,
        "avg_answer_tokens": Decimal("500"),
        "usd_per_million_output_tokens": Decimal("0.42"),
        "usd_idr_rate": Decimal("16000"),
        "safety_margin_percent": Decimal("20"),
    }
    nilai.update(ubah)
    return cost_estimator.estimate_cost_idr(**nilai)


def test_biaya_memakai_token_harga_kurs_dan_margin():
    # Arrange: 60 panggilan x 500 token = 30.000 token
    # 30.000 / 1.000.000 x 0,42 USD = 0,0126 USD, x 16.000 = 201,60 rupiah, +20%

    # Act
    biaya = _hitung()

    # Assert
    assert biaya == Decimal("241.92")


def test_margin_nol_tidak_menambah_biaya():
    # Arrange + Act
    biaya = _hitung(safety_margin_percent=Decimal("0"))

    # Assert
    assert biaya == Decimal("201.60")


def test_tanpa_panggilan_biayanya_nol():
    # Arrange + Act
    biaya = _hitung(total_calls=0)

    # Assert
    assert biaya == Decimal("0.00")


def test_pecahan_sen_dibulatkan_ke_atas_agar_tidak_meremehkan():
    # Arrange: 1 token x 1 USD per juta x kurs 1 = 0,000001, jauh di bawah satu sen

    # Act
    biaya = _hitung(
        total_calls=1,
        avg_answer_tokens=Decimal("1"),
        usd_per_million_output_tokens=Decimal("1"),
        usd_idr_rate=Decimal("1"),
        safety_margin_percent=Decimal("0"),
    )

    # Assert
    assert biaya == Decimal("0.01")


def test_hasil_berupa_decimal_dua_angka_di_belakang_koma():
    # Arrange + Act
    biaya = _hitung()

    # Assert
    assert isinstance(biaya, Decimal)
    assert biaya.as_tuple().exponent == -2


def test_rata_rata_token_boleh_pecahan_dan_perhitungan_tetap_tepat():
    # Arrange: 1.500 panggilan x 2.000,5 token = 3.000.750 token
    # = 30,0075 USD pada 10 USD per juta, x 16.500 = 495.123,75 rupiah

    # Act
    biaya = _hitung(
        total_calls=1500,
        avg_answer_tokens=Decimal("2000.5"),
        usd_per_million_output_tokens=Decimal("10"),
        usd_idr_rate=Decimal("16500"),
        safety_margin_percent=Decimal("0"),
    )

    # Assert
    assert biaya == Decimal("495123.75")


@pytest.mark.parametrize(
    "ubah",
    [
        {"usd_idr_rate": Decimal("0")},
        {"usd_idr_rate": Decimal("-1")},
        {"usd_per_million_output_tokens": Decimal("-0.1")},
        {"avg_answer_tokens": Decimal("-1")},
        {"safety_margin_percent": Decimal("-5")},
        {"total_calls": -1},
    ],
)
def test_masukan_yang_tidak_masuk_akal_adalah_kesalahan_konfigurasi_500(ubah):
    # Arrange: kurs nol atau angka negatif hanya bisa datang dari konfigurasi rusak

    # Act
    with pytest.raises(InternalError) as galat:
        _hitung(**ubah)

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "COST_ESTIMATE_INVALID_INPUT"
