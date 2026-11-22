"""QA alur tren skor satu produk antar bulan. PBI-17 (SCRUM-84) AC1-AC4, AC6, AC7; SCRUM-336.

GET /reports/trend di atas sesi sungguhan (tests/trend_qa_pg.py): sesi dijalankan worker atau
diisi Kontributor, dinilai, lalu dimundurkan ke bulannya. Desain: equivalence partition untuk
metrik dan rentang, boundary value untuk batas rentang (bulan ke-3, ke-6, ke-12), dan
perbandingan angka tren dengan angka laporan dari sesi yang sama.

Label: Positif = jalur normal, Edge = batas perilaku.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.modules.reports.wib import WIB
from tests.trend_qa_pg import (
    HANYA_POSTGRES,
    KONTRIBUTOR,
    VIEWER,
    ProviderTren,
    angka,
    awal_bulan,
    buat_laporan,
    bulan_titik,
    sesi_manual,
    sesi_otomatis,
    titik,
    tren,
    waktu_di_bulan,
)

pytestmark = HANYA_POSTGRES


def _bulan(*mundur: int) -> list[str]:
    return [awal_bulan(m).isoformat() for m in mundur]


def test_beberapa_sesi_menjadi_satu_garis_satu_titik_per_bulan(dunia_tren):
    """Positif: AC1, tiga bulan diukur menjadi satu garis dengan satu titik per bulan, urut dari
    bulan paling awal; Viewer dan Kontributor membaca tren skor test yang sama dengan Admin."""
    # Arrange
    for mundur, mutu in ((2, "buruk"), (1, "sedang"), (0, "baik")):
        sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(mundur), provider=ProviderTren(mutu))

    # Act
    admin = tren(dunia_tren, "AiYU")
    pembaca = {o.nama: tren(dunia_tren, "AiYU", o) for o in (VIEWER, KONTRIBUTOR)}

    # Assert
    assert bulan_titik(admin) == _bulan(2, 1, 0)
    assert admin.json()["score_scope"] == "test"
    nilai = [Decimal(p["value"]) for p in titik(admin)]
    assert nilai == sorted(nilai), "mutu jawaban naik tiap bulan, jadi skornya juga naik"
    for respons in pembaca.values():
        assert respons.json() == admin.json()


def test_dua_sesi_di_bulan_yang_sama_memakai_sesi_terbaru(dunia_tren):
    """Positif: AC4, bila satu bulan punya dua sesi layak, titiknya dari sesi yang terbaru."""
    # Arrange
    sesi_otomatis(
        dunia_tren,
        "AiYU",
        kapan=waktu_di_bulan(0, hari=3),
        provider=ProviderTren("buruk"),
        label="AiYU awal bulan",
    )
    sesi_otomatis(
        dunia_tren,
        "AiYU",
        kapan=waktu_di_bulan(0, hari=20),
        provider=ProviderTren("baik"),
        label="AiYU akhir bulan",
    )

    # Act
    (satu,) = titik(tren(dunia_tren, "AiYU"))

    # Assert
    assert (satu["month"], satu["label"], satu["value"]) == (
        _bulan(0)[0],
        "AiYU akhir bulan",
        "1.00",
    )


def test_bulan_titik_dari_waktu_selesai_dalam_wib(dunia_tren):
    """Edge: AC4 dan D4 (keputusan SA, #153), sesi yang selesai pukul 00.30 WIB tanggal 1 masuk
    bulan baru walaupun di UTC masih tanggal terakhir bulan sebelumnya."""
    # Arrange
    awal = awal_bulan(0)
    tengah_malam = datetime(awal.year, awal.month, 1, 0, 30, tzinfo=WIB)
    sesi_otomatis(dunia_tren, "AiYU", kapan=tengah_malam, label="AiYU lewat tengah malam")

    # Act
    (satu,) = titik(tren(dunia_tren, "AiYU"))

    # Assert
    assert tengah_malam.astimezone(UTC).month != awal.month
    assert (satu["month"], satu["label"]) == (awal.isoformat(), "AiYU lewat tengah malam")


def test_produk_baru_dimulai_dari_bulan_pertama_diukur(dunia_tren):
    """Edge: AC6, produk yang baru diukur bulan ini tidak mendapat titik kosong untuk bulan
    sebelumnya, walaupun produk lain sudah diukur sejak lama."""
    # Arrange
    sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(5))
    sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(0))
    sesi_otomatis(dunia_tren, "Justisio", kapan=waktu_di_bulan(0))

    # Act
    lama = tren(dunia_tren, "AiYU")
    baru = tren(dunia_tren, "Justisio")

    # Assert
    assert bulan_titik(lama) == _bulan(5, 0)
    assert bulan_titik(baru) == _bulan(0)


# Bulan yang diukur, dihitung mundur dari bulan ini (0). Setiap batas punya dua sisi: bulan
# terakhir yang masih masuk (2, 5, 11) dan bulan pertama yang sudah keluar (3, 6, 12), jadi
# rentang yang meleset satu bulan ke arah mana pun membuat tes gagal (review #155).
DIUKUR = (13, 12, 11, 6, 5, 3, 2, 0)


@pytest.mark.parametrize(
    ("rentang", "mundur"),
    [
        # 3m = bulan ini dan dua bulan sebelumnya: 2 bulan lalu masuk, 3 bulan lalu keluar.
        ("3m", (2, 0)),
        # 6m: 5 bulan lalu masuk, 6 bulan lalu keluar.
        ("6m", (5, 3, 2, 0)),
        # 12m: 11 bulan lalu masuk, 12 bulan lalu keluar.
        ("12m", (11, 6, 5, 3, 2, 0)),
        # all: tanpa batas, termasuk 12 dan 13 bulan lalu.
        ("all", DIUKUR),
    ],
)
def test_rentang_waktu_memotong_bulan_kalender_terakhir(dunia_tren, rentang, mundur):
    """Positif dan Edge: AC2, rentang 3, 6, 12 bulan atau semua, dihitung dalam bulan kalender
    termasuk bulan ini; bawaannya semua. Boundary value: bulan terakhir yang masih masuk dan
    bulan pertama yang sudah keluar diukur dua-duanya."""
    # Arrange
    for m in DIUKUR:
        sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(m))

    # Act
    respons = tren(dunia_tren, "AiYU", range=rentang)
    bawaan = tren(dunia_tren, "AiYU")

    # Assert
    assert bulan_titik(respons) == _bulan(*mundur)
    assert respons.json()["range"] == rentang
    assert bawaan.json()["range"] == "all"


def _dua_desimal(nilai: Decimal) -> str:
    return str(nilai.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def test_setiap_metrik_satu_garis_dan_sama_dengan_angka_laporan(dunia_tren):
    """Positif: AC3, metrik total (bawaan), deterministik, dan penilai LLM masing-masing satu
    garis; nilainya sama dengan angka laporan dari sesi yang sama, dan total adalah rata-rata
    keduanya. Edge: AC7, respons tidak punya garis target apa pun."""
    # Arrange
    aiyu = sesi_otomatis(
        dunia_tren, "AiYU", kapan=waktu_di_bulan(0), provider=ProviderTren("sedang")
    )
    legalhero = sesi_manual(dunia_tren, "LegalHero", kapan=waktu_di_bulan(0))
    laporan = buat_laporan(dunia_tren, [aiyu, legalhero])
    di_laporan = angka(laporan, "AiYU", "test")

    # Act
    per_metrik = {m: tren(dunia_tren, "AiYU", metric=m) for m in ("total", "deterministic", "llm")}
    bawaan = tren(dunia_tren, "AiYU")

    # Assert
    (det,) = titik(per_metrik["deterministic"])
    (llm,) = titik(per_metrik["llm"])
    (total,) = titik(per_metrik["total"])
    assert (det["value"], llm["value"]) == (di_laporan["det_score"], di_laporan["llm_score"])
    rata_rata = (Decimal(di_laporan["det_score"]) + Decimal(di_laporan["llm_score"])) / 2
    assert total["value"] == _dua_desimal(rata_rata)
    assert bawaan.json()["metric"] == "total"
    assert set(bawaan.json()) == {
        "product_id",
        "product_name",
        "score_scope",
        "metric",
        "range",
        "points",
        "warnings",
    }
