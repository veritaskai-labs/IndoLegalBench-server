"""Meter pemakaian penilai: harga, sisa anggaran bulanan, dan catatan pemakaian. SCRUM-271."""

from decimal import Decimal
from types import SimpleNamespace

from app.modules.runs.judge import JudgeRow
from app.modules.runs.judge_budget import JudgeUsage, open_judge_meter

_RATE = Decimal("16000")


class _Rates:
    def __init__(self, rate: Decimal = _RATE) -> None:
        self.calls = 0
        self._rate = rate

    def usd_idr(self) -> Decimal:
        self.calls += 1
        return self._rate


def _ikatan(budget="100000", harga_masuk="1", harga_keluar="2"):
    return SimpleNamespace(
        price_input_usd_per_mtok=None if harga_masuk is None else Decimal(harga_masuk),
        price_output_usd_per_mtok=None if harga_keluar is None else Decimal(harga_keluar),
        monthly_budget_idr=Decimal(budget),
    )


def test_meter_memakai_harga_produk_dan_kurs_hari_ini():
    """Positif: JudgePrices disusun dari harga produk penilai dan kurs dari penyedia kurs."""
    # Arrange
    rates = _Rates()

    # Act
    meter = open_judge_meter(binding=_ikatan(), spent_idr=Decimal("0"), rates=rates)

    # Assert
    assert meter.prices.usd_per_million_input_tokens == Decimal("1")
    assert meter.prices.usd_per_million_output_tokens == Decimal("2")
    assert meter.prices.usd_idr_rate == _RATE
    assert rates.calls == 1


def test_meter_punya_anggaran_selama_pengeluaran_di_bawah_batas():
    """Positif: sisa anggaran adalah batas bulanan dikurangi pengeluaran bulan ini."""
    # Arrange
    ikatan = _ikatan(budget="100000")

    # Act
    meter = open_judge_meter(binding=ikatan, spent_idr=Decimal("99999.99"), rates=_Rates())

    # Assert
    assert meter.has_budget() is True


def test_meter_tanpa_anggaran_saat_pengeluaran_sama_dengan_batas():
    """Edge: pengeluaran tepat sama dengan batas berarti anggaran habis."""
    # Arrange
    ikatan = _ikatan(budget="100000")

    # Act
    meter = open_judge_meter(binding=ikatan, spent_idr=Decimal("100000"), rates=_Rates())

    # Assert
    assert meter.has_budget() is False


def test_meter_tanpa_anggaran_saat_pengeluaran_melebihi_batas():
    """Negatif: pengeluaran di atas batas tetap berarti habis, bukan sisa negatif yang lolos."""
    # Arrange
    ikatan = _ikatan(budget="100")

    # Act
    meter = open_judge_meter(binding=ikatan, spent_idr=Decimal("250"), rates=_Rates())

    # Assert
    assert meter.has_budget() is False


def test_charge_mengurangi_sisa_hingga_habis():
    """Positif: biaya satu panggilan dipotong dari sisa sehingga panggilan berikutnya bisa ditolak."""
    # Arrange
    meter = open_judge_meter(binding=_ikatan(budget="100"), spent_idr=Decimal("0"), rates=_Rates())

    # Act
    meter.charge(Decimal("60"))
    sisa_pertama = meter.has_budget()
    meter.charge(Decimal("40"))

    # Assert
    assert sisa_pertama is True
    assert meter.has_budget() is False


def test_charge_tanpa_biaya_tidak_mengubah_sisa():
    """Edge: panggilan tanpa token (biaya kosong) tidak memotong anggaran."""
    # Arrange
    meter = open_judge_meter(binding=_ikatan(budget="100"), spent_idr=Decimal("99"), rates=_Rates())

    # Act
    meter.charge(None)

    # Assert
    assert meter.has_budget() is True


def test_harga_kosong_diteruskan_apa_adanya_ke_prices():
    """Edge: harga produk boleh kosong, penolakan ada di perhitungan biaya, bukan di meter."""
    # Arrange
    ikatan = _ikatan(harga_masuk=None, harga_keluar=None)

    # Act
    meter = open_judge_meter(binding=ikatan, spent_idr=Decimal("0"), rates=_Rates())

    # Assert
    assert meter.prices.usd_per_million_input_tokens is None
    assert meter.prices.usd_per_million_output_tokens is None


def test_usage_diambil_dari_baris_judge():
    """Positif: token, biaya, dan kurs baris judge disalin ke catatan pemakaian."""
    # Arrange
    baris = JudgeRow(
        kind="quality",
        repetition=1,
        prompt="p",
        input_tokens=120,
        output_tokens=30,
        cost_idr=Decimal("12.50"),
        usd_idr_rate=_RATE,
        failure_kind="provider_error",
    )

    # Act
    usage = JudgeUsage.from_row(baris)

    # Assert
    assert usage == JudgeUsage(
        input_tokens=120,
        output_tokens=30,
        cost_idr=Decimal("12.50"),
        usd_idr_rate=_RATE,
        failure_kind="provider_error",
    )
