"""Perkiraan biaya penilaian sebelum tombol Nilai. PBI-14 AC6, SCRUM-78 SCRUM-286.

Fungsi estimasi murni: tanpa database, jaringan, atau penyedia kurs. Biaya tiap panggilan
memakai rumus biaya nyata (actual_cost_idr), jadi estimasi dan biaya sesungguhnya tidak
berbeda cara hitung.

Label: Positif = angka sesuai rumus, Negatif = masukan ditolak,
Edge = nol percobaan, harga nol, pembulatan per panggilan.
"""

from decimal import Decimal

import pytest

from app.modules.runs.call_cost import actual_cost_idr
from app.modules.runs.grade_estimate import JUDGE_CALLS_PER_ATTEMPT, estimate_grade
from app.modules.runs.judge import JudgePrices
from app.modules.runs.judge_budget import JudgePricingMissing
from app.shared.exceptions import InternalError

_RATE = Decimal("16000")


def _harga(masuk="1", keluar="2", kurs=_RATE) -> JudgePrices:
    return JudgePrices(
        usd_per_million_input_tokens=None if masuk is None else Decimal(masuk),
        usd_per_million_output_tokens=None if keluar is None else Decimal(keluar),
        usd_idr_rate=kurs,
    )


def test_dua_panggilan_penilai_per_percobaan():
    """Positif: kualitas dan format, jadi dua panggilan untuk tiap percobaan."""
    # Act & Assert
    assert JUDGE_CALLS_PER_ATTEMPT == 2


def test_estimasi_menjumlahkan_percobaan_panggilan_token_dan_biaya():
    """Positif: dua percobaan, empat panggilan, biaya adalah jumlah biaya tiap panggilan."""
    # Arrange
    token_prompt = [(100, 120), (80, 90)]

    # Act
    hasil = estimate_grade(
        attempt_prompt_tokens=token_prompt, output_tokens_per_call=50, prices=_harga()
    )

    # Assert
    assert hasil.attempts_to_grade == 2
    assert hasil.judge_calls == 4
    assert hasil.input_tokens == 390
    assert hasil.output_tokens == 200
    # (100+50*2)/1e6*16000=3.20, 3.52, 2.88, 3.04
    assert hasil.cost_idr == Decimal("12.64")


def test_biaya_dibulatkan_per_panggilan_seperti_meter():
    """Edge: 1 token masuk bernilai 0.016, dibulatkan 0.02 per panggilan, bukan 0.064 sekali."""
    # Arrange
    harga = _harga()
    per_panggilan = actual_cost_idr(
        input_tokens=1,
        output_tokens=0,
        usd_per_million_input_tokens=harga.usd_per_million_input_tokens,
        usd_per_million_output_tokens=harga.usd_per_million_output_tokens,
        usd_idr_rate=harga.usd_idr_rate,
    )

    # Act
    hasil = estimate_grade(
        attempt_prompt_tokens=[(1, 1), (1, 1)], output_tokens_per_call=0, prices=harga
    )

    # Assert
    assert per_panggilan == Decimal("0.02")
    assert hasil.cost_idr == Decimal("0.08")


def test_tanpa_percobaan_layak_estimasi_nol_bukan_galat():
    """Edge: semua percobaan gangguan teknis berarti tidak ada panggilan dan biaya nol."""
    # Act
    hasil = estimate_grade(attempt_prompt_tokens=[], output_tokens_per_call=300, prices=_harga())

    # Assert
    assert (hasil.attempts_to_grade, hasil.judge_calls) == (0, 0)
    assert (hasil.input_tokens, hasil.output_tokens) == (0, 0)
    assert hasil.cost_idr == Decimal("0.00")


def test_harga_nol_sah_dan_biaya_nol():
    """Edge: model gratis boleh berharga nol, token tetap dihitung."""
    # Act
    hasil = estimate_grade(
        attempt_prompt_tokens=[(100, 100)],
        output_tokens_per_call=50,
        prices=_harga(masuk="0", keluar="0"),
    )

    # Assert
    assert hasil.cost_idr == Decimal("0.00")
    assert hasil.input_tokens == 200
    assert hasil.output_tokens == 100


@pytest.mark.parametrize("ubah", [{"masuk": None}, {"keluar": None}])
def test_harga_tidak_lengkap_ditolak_jelas(ubah):
    """Negatif: harga masuk atau keluar kosong berarti JudgePricingMissing, bukan biaya tebakan."""
    # Act
    with pytest.raises(JudgePricingMissing):
        estimate_grade(
            attempt_prompt_tokens=[(10, 10)], output_tokens_per_call=5, prices=_harga(**ubah)
        )


@pytest.mark.parametrize(
    "token_prompt, output_per_panggilan",
    [([(-1, 10)], 5), ([(10, 10)], -1)],
)
def test_angka_negatif_adalah_galat_konfigurasi_500(token_prompt, output_per_panggilan):
    """Negatif: token negatif hanya bisa datang dari kode atau konfigurasi rusak, bukan 422."""
    # Act
    with pytest.raises(InternalError) as galat:
        estimate_grade(
            attempt_prompt_tokens=token_prompt,
            output_tokens_per_call=output_per_panggilan,
            prices=_harga(),
        )

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "COST_ESTIMATE_INVALID_INPUT"


def test_kurs_tidak_positif_adalah_galat_konfigurasi_500():
    """Negatif: kurs nol ditolak dengan galat server yang sama seperti biaya nyata."""
    # Act
    with pytest.raises(InternalError) as galat:
        estimate_grade(
            attempt_prompt_tokens=[(10, 10)],
            output_tokens_per_call=5,
            prices=_harga(kurs=Decimal("0")),
        )

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "COST_ESTIMATE_INVALID_INPUT"
