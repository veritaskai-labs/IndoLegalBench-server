"""Create-session summary: cases, calls, cost and budget. SCRUM-76 SCRUM-206.

PBI-11 AC2, AC3 and AC5. Every dependency is a fake: the product, the case
counts, the month's spending, the answer length history, the price catalog
and the exchange rate. Nothing here touches a database or the network.

Reference numbers used below: 10 USD per million output tokens, a rate of
16.000 rupiah, a 20 percent margin and 500 answer tokens on average. For
25 cases times 3 repeats that is 75 calls, 37.500 tokens, 0,375 USD,
6.000 rupiah and 7.200,00 rupiah with the margin.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.runs import exchange_rate, pricing, schemas, service, summary_ports
from app.shared.exceptions import InternalError, NotFoundError, ValidationError

SUITE_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SUITE_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
PRODUCT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


def _produk(**ubah):
    nilai = {
        "id": PRODUCT_ID,
        "name": "AiYU",
        "measurement_type": "automatic",
        "is_active": True,
        "provider_type": "openai_compatible",
        "model_name": "deepseek-flash",
        "monthly_budget_idr": Decimal("500000.00"),
        "price_output_usd_per_mtok": Decimal("10"),
    }
    nilai.update(ubah)
    return summary_ports.ProductInfo(**nilai)


class _Produk:
    def __init__(self, produk) -> None:
        self.produk = produk
        self.diminta: list[uuid.UUID] = []

    def get(self, product_id):
        self.diminta.append(product_id)
        return self.produk


class _Kasus:
    def __init__(self, counts, galat=None) -> None:
        self.counts = counts
        self.galat = galat
        self.panggilan: list[set[uuid.UUID]] = []

    def approved_counts(self, suite_ids):
        self.panggilan.append(set(suite_ids))
        if self.galat is not None:
            raise self.galat
        return self.counts


class _Pengeluaran:
    def __init__(self, terpakai) -> None:
        self.terpakai = terpakai
        self.diminta: list[uuid.UUID] = []

    def spent_this_month_idr(self, product_id):
        self.diminta.append(product_id)
        return self.terpakai


class _Riwayat:
    def __init__(self, rata_rata) -> None:
        self.rata_rata = rata_rata

    def average_answer_tokens(self, product_id):
        return self.rata_rata


class _Kurs:
    def __init__(self, nilai: Decimal | None) -> None:
        self.dalam = exchange_rate.ConfiguredExchangeRate(nilai)
        self.panggilan = 0

    def usd_idr(self):
        self.panggilan += 1
        return self.dalam.usd_idr()


def _bangun(
    *,
    produk=None,
    counts=None,
    galat_kasus=None,
    terpakai=Decimal("100000.00"),
    rata_rata=Decimal("500"),
    katalog=None,
    kurs=Decimal("16000"),
    margin=Decimal("20"),
    rata_rata_bawaan=None,
):
    fakes = SimpleNamespace(
        produk=_Produk(_produk() if produk is None else produk),
        kasus=_Kasus({"dev": 20, "test": 5} if counts is None else counts, galat_kasus),
        pengeluaran=_Pengeluaran(terpakai),
        riwayat=_Riwayat(rata_rata),
        kurs=_Kurs(kurs),
    )
    layanan = service.SummaryService(
        products=fakes.produk,
        cases=fakes.kasus,
        spend=fakes.pengeluaran,
        history=fakes.riwayat,
        prices=pricing.ConfigPriceCatalog({} if katalog is None else katalog),
        rates=fakes.kurs,
        safety_margin_percent=margin,
        default_avg_answer_tokens=rata_rata_bawaan,
    )
    return layanan, fakes


def _minta(**ubah) -> schemas.SummaryRequest:
    nilai = {
        "suite_ids": [SUITE_A],
        "product_id": PRODUCT_ID,
        "repeat_count": 3,
        "case_filter": "both",
    }
    nilai.update(ubah)
    return schemas.SummaryRequest(**nilai)


def test_ringkasan_memuat_kasus_panggilan_pilihan_biaya_dan_sisa_anggaran():
    # Arrange: 20 dev dan 5 test, 3 pengulangan, sudah terpakai 100.000
    layanan, _ = _bangun()

    # Act
    hasil = layanan.summarize(_minta())

    # Assert
    assert hasil.case_count == 25
    assert hasil.total_calls == 75
    assert hasil.case_filter == schemas.CaseFilter.BOTH
    assert hasil.repeat_count == 3
    assert hasil.estimated_cost_idr == Decimal("7200.00")
    assert hasil.remaining_budget_idr == Decimal("400000.00")


def test_filter_dev_hanya_menghitung_kasus_dev():
    # Arrange
    layanan, _ = _bangun()

    # Act
    hasil = layanan.summarize(_minta(case_filter="dev"))

    # Assert: 20 kasus x 3 = 60 panggilan, 30.000 token
    assert hasil.case_count == 20
    assert hasil.total_calls == 60
    assert hasil.case_filter == schemas.CaseFilter.DEV
    assert hasil.estimated_cost_idr == Decimal("5760.00")


def test_pengulangan_lima_menambah_panggilan_dan_biaya():
    # Arrange
    layanan, _ = _bangun()

    # Act
    hasil = layanan.summarize(_minta(repeat_count=5))

    # Assert: 25 x 5 = 125 panggilan, 62.500 token
    assert hasil.total_calls == 125
    assert hasil.estimated_cost_idr == Decimal("12000.00")


def test_suite_dan_produk_diteruskan_ke_dependensi_yang_tepat():
    # Arrange
    layanan, fakes = _bangun()

    # Act
    layanan.summarize(_minta(suite_ids=[SUITE_A, SUITE_B, SUITE_A]))

    # Assert
    assert fakes.kasus.panggilan == [{SUITE_A, SUITE_B}]
    assert fakes.produk.diminta == [PRODUCT_ID]
    assert fakes.pengeluaran.diminta == [PRODUCT_ID]
    assert fakes.kurs.panggilan == 1


def test_harga_produk_dipakai_lebih_dulu_daripada_katalog():
    # Arrange: katalog menyebut 20, produk menyebut 10
    katalog = {"openai_compatible:deepseek-flash": Decimal("20")}
    layanan, _ = _bangun(katalog=katalog)

    # Act
    hasil = layanan.summarize(_minta())

    # Assert
    assert hasil.estimated_cost_idr == Decimal("7200.00")


def test_katalog_dipakai_saat_produk_belum_punya_harga():
    # Arrange
    katalog = {"openai_compatible:deepseek-flash": Decimal("20")}
    layanan, _ = _bangun(
        produk=_produk(price_output_usd_per_mtok=None),
        katalog=katalog,
    )

    # Act
    hasil = layanan.summarize(_minta())

    # Assert: harga 20 dua kali lipat dari 10
    assert hasil.estimated_cost_idr == Decimal("14400.00")


def test_tanpa_harga_produk_dan_tanpa_katalog_adalah_kesalahan_konfigurasi_500():
    # Arrange
    layanan, _ = _bangun(produk=_produk(price_output_usd_per_mtok=None))

    # Act
    with pytest.raises(InternalError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "PRICE_NOT_CONFIGURED"


def test_tanpa_riwayat_memakai_rata_rata_token_bawaan_konfigurasi():
    # Arrange: belum ada pengukuran sebelumnya
    layanan, _ = _bangun(rata_rata=None, rata_rata_bawaan=Decimal("1000"))

    # Act
    hasil = layanan.summarize(_minta())

    # Assert: 75 panggilan x 1.000 token, bukan 500
    assert hasil.estimated_cost_idr == Decimal("14400.00")


def test_tanpa_riwayat_dan_tanpa_bawaan_adalah_kesalahan_konfigurasi_500():
    # Arrange
    layanan, _ = _bangun(rata_rata=None, rata_rata_bawaan=None)

    # Act
    with pytest.raises(InternalError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.status_code == 500
    assert galat.value.code == "AVG_ANSWER_TOKENS_NOT_CONFIGURED"


def test_produk_tidak_ditemukan_atau_terhapus_dijawab_404_tanpa_menyentuh_kasus():
    # Arrange: port produk mengembalikan None untuk id yang tidak ada atau terhapus
    layanan, fakes = _bangun()
    fakes.produk.produk = None

    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.status_code == 404
    assert galat.value.code == "AI_PRODUCT_NOT_FOUND"
    assert fakes.kasus.panggilan == []


def test_produk_manual_ditolak_422_tanpa_menyentuh_kasus():
    # Arrange
    manual = _produk(
        measurement_type="manual",
        provider_type=None,
        model_name=None,
        monthly_budget_idr=None,
        price_output_usd_per_mtok=None,
    )
    layanan, fakes = _bangun(produk=manual)

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "PRODUCT_NOT_AUTOMATIC"
    assert fakes.kasus.panggilan == []


def test_produk_nonaktif_ditolak_422():
    # Arrange
    layanan, fakes = _bangun(produk=_produk(is_active=False))

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "PRODUCT_INACTIVE"
    assert fakes.kasus.panggilan == []


def test_suite_yang_tidak_ada_diteruskan_sebagai_404_dari_port_kasus():
    # Arrange
    tidak_ada = NotFoundError("Suite tidak ditemukan", code="SUITE_NOT_FOUND")
    layanan, _ = _bangun(galat_kasus=tidak_ada)

    # Act
    with pytest.raises(NotFoundError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value is tidak_ada


@pytest.mark.parametrize(
    ("counts", "pilihan"),
    [({"dev": 0, "test": 0}, "both"), ({"dev": 20, "test": 0}, "test"), ({}, "dev")],
)
def test_tanpa_kasus_yang_layak_ditolak_422_sebelum_kurs_dan_anggaran(counts, pilihan):
    # Arrange
    layanan, fakes = _bangun(counts=counts)

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.summarize(_minta(case_filter=pilihan))

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "NO_ELIGIBLE_CASES"
    assert fakes.kurs.panggilan == 0
    assert fakes.pengeluaran.diminta == []


def test_biaya_melebihi_sisa_anggaran_ditolak_422_dengan_nama_produk_dan_sisa():
    # Arrange: terpakai 495.000 dari 500.000, sisa 5.000 < perkiraan 7.200
    layanan, _ = _bangun(terpakai=Decimal("495000.00"))

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.status_code == 422
    assert galat.value.code == "BUDGET_EXCEEDED"
    assert "AiYU" in galat.value.message
    assert galat.value.details["product_name"] == "AiYU"
    assert galat.value.details["remaining_budget_idr"] == "5000.00"
    assert galat.value.details["estimated_cost_idr"] == "7200.00"


def test_biaya_sama_dengan_sisa_anggaran_masih_boleh_karena_aturannya_melebihi():
    # Arrange: sisa tepat 7.200,00
    layanan, _ = _bangun(terpakai=Decimal("492800.00"))

    # Act
    hasil = layanan.summarize(_minta())

    # Assert
    assert hasil.estimated_cost_idr == Decimal("7200.00")
    assert hasil.remaining_budget_idr == Decimal("7200.00")


def test_pengeluaran_melewati_anggaran_menampilkan_sisa_nol_bukan_negatif():
    # Arrange: terpakai 600.000 dari 500.000
    layanan, _ = _bangun(terpakai=Decimal("600000.00"))

    # Act
    with pytest.raises(ValidationError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.code == "BUDGET_EXCEEDED"
    assert galat.value.details["remaining_budget_idr"] == "0.00"


def test_kurs_yang_tidak_tersedia_adalah_kesalahan_konfigurasi_500():
    # Arrange
    layanan, _ = _bangun(kurs=None)

    # Act
    with pytest.raises(InternalError) as galat:
        layanan.summarize(_minta())

    # Assert
    assert galat.value.code == "EXCHANGE_RATE_NOT_CONFIGURED"
