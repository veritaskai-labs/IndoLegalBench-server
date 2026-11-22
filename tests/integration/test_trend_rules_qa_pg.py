"""QA garis putus, penanda, dan peringatan tren. PBI-17 (SCRUM-84) AC5, AC11, AC13; SCRUM-336.

Setiap tes membuat dua sesi berurutan bulan lalu dan bulan ini (tests/trend_qa_pg.py), lalu
mengubah tepat satu hal di antaranya: versi rubrik, model penilai, suite, versi kasus, atau
versi model produk. Desain: base choice (dua sesi yang setara tidak memutus garis), lalu satu
karakteristik diganti per kasus uji supaya kegagalan menunjuk karakteristik itu.

Label: Positif = jalur normal, Negatif = harus ditolak, Edge = batas perilaku.
"""

import pytest
from sqlalchemy import func, select

from app.modules.grading.models import GradingRun
from app.modules.runs.models import Attempt, CallLog
from tests.trend_qa_pg import (
    DEV_AI,
    HANYA_POSTGRES,
    PenilaiTrenBaru,
    ProviderTren,
    buat_suite,
    rubrik_baru,
    sesi_manual,
    sesi_otomatis,
    titik,
    tren,
    versi_kasus_baru,
    waktu_di_bulan,
)

pytestmark = HANYA_POSTGRES


def _ganti_suite(d) -> dict:
    d.suite_id = buat_suite(d, "Tren ketenagakerjaan B", akhiran="-B")
    return {}


# Satu perubahan di antara dua sesi -> argumen sesi kedua, dan alasan putus yang diharapkan.
PERUBAHAN = {
    "versi rubrik": (lambda d: rubrik_baru(d) or {}, ["RUBRIC_VERSION"]),
    "model penilai": (lambda d: d.pakai_penilai(PenilaiTrenBaru) or {}, ["JUDGE_MODEL"]),
    "suite": (_ganti_suite, ["SUITE_VERSION"]),
    "versi kasus test": (lambda d: versi_kasus_baru(d, "TRN-001") or {}, ["SUITE_VERSION"]),
    "versi model produk": (
        lambda d: {"provider": ProviderTren(model="aiyu-2026-12")},
        ["MODEL_VERSION_DIFFERS"],
    ),
}


def _dua_sesi(d, ubah=lambda d: {}) -> None:
    sesi_otomatis(d, "AiYU", kapan=waktu_di_bulan(1))
    sesi_otomatis(d, "AiYU", kapan=waktu_di_bulan(0), **ubah(d))


def test_dua_sesi_yang_setara_menjadi_satu_garis_tanpa_putus(dunia_tren):
    """Positif: base choice AC5, tanpa perubahan versi garisnya bersambung, tanpa alasan putus,
    dan kedua titik memakai kasus yang sama."""
    # Arrange
    _dua_sesi(dunia_tren)

    # Act
    lalu, kini = titik(tren(dunia_tren, "AiYU"))

    # Assert
    assert (lalu["breaks"], kini["breaks"]) == ([], [])
    assert (lalu["case_count"], kini["case_count"]) == (2, 2)


@pytest.mark.parametrize("perubahan", list(PERUBAHAN))
def test_perubahan_versi_memutus_garis_di_titik_sesudahnya(dunia_tren, perubahan):
    """Edge: AC5, versi rubrik, model penilai, suite, versi kasus, atau versi model produk yang
    berubah memutus garis tepat di titik sesudah perubahan, dengan alasannya. Titik pertama
    tidak pernah diputus, dan kedua titik tetap punya nilai."""
    # Arrange
    ubah, alasan = PERUBAHAN[perubahan]
    _dua_sesi(dunia_tren, ubah)

    # Act
    lalu, kini = titik(tren(dunia_tren, "AiYU"))

    # Assert
    assert (lalu["breaks"], kini["breaks"]) == ([], alasan)
    assert lalu["value"] is not None and kini["value"] is not None


@pytest.mark.parametrize("perubahan", ["versi rubrik", "model penilai", "suite"])
def test_tren_dev_juga_diputus_saat_penilai_atau_suite_berbeda(dunia_tren, perubahan):
    """Negatif: AC13, tren dev untuk produk mana pun memakai syarat PBI-15 yang sama; sesi dengan
    versi rubrik, model penilai, atau suite berbeda tidak disambung ke garis sebelumnya."""
    # Arrange
    ubah, alasan = PERUBAHAN[perubahan]
    _dua_sesi(dunia_tren, ubah)

    # Act
    lalu, kini = titik(tren(dunia_tren, "AiYU", DEV_AI, score_scope="dev"))

    # Assert
    assert kini["breaks"] == alasan
    assert lalu["breaks"] == []


def test_versi_kasus_dev_berubah_tidak_memutus_tren_test(dunia_tren):
    """Edge: AC5 dan AC12, perubahan pada kasus dev hanya memutus tren dev; tren test tidak
    berubah karena kasus di luar jenisnya tidak ikut dihitung."""
    # Arrange
    _dua_sesi(dunia_tren, lambda d: versi_kasus_baru(d, "TRN-003") or {})

    # Act
    tes = titik(tren(dunia_tren, "AiYU"))
    dev = titik(tren(dunia_tren, "AiYU", DEV_AI, score_scope="dev"))

    # Assert
    assert [p["breaks"] for p in tes] == [[], []]
    assert [p["breaks"] for p in dev] == [[], ["SUITE_VERSION"]]


def test_sesi_manual_dengan_jumlah_percobaan_berbeda_diberi_penanda(dunia_tren):
    """Edge: AC5, sesi manual dengan jumlah percobaan berbeda diberi penanda di setiap titiknya,
    tanpa memutus garis; bulan titiknya dari waktu sesi ditandai Selesai."""
    # Arrange
    sesi_manual(dunia_tren, "LegalHero", kapan=waktu_di_bulan(1), pengulangan=1)
    sesi_manual(dunia_tren, "LegalHero", kapan=waktu_di_bulan(0), pengulangan=2)

    # Act
    lalu, kini = titik(tren(dunia_tren, "LegalHero"))

    # Assert
    assert (lalu["markers"], kini["markers"]) == (["REPETITIONS_DIFFER"], ["REPETITIONS_DIFFER"])
    assert (lalu["breaks"], kini["breaks"]) == ([], [])


def test_sesi_otomatis_dengan_jumlah_percobaan_berbeda_tanpa_penanda(dunia_tren):
    """Edge: AC5, penanda jumlah percobaan hanya untuk sesi manual."""
    # Arrange
    sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(1), repeat_count=1)
    sesi_otomatis(dunia_tren, "AiYU", kapan=waktu_di_bulan(0), repeat_count=2)

    # Act
    lalu, kini = titik(tren(dunia_tren, "AiYU"))

    # Assert
    assert (lalu["markers"], kini["markers"]) == ([], [])


def _jumlah(d, model) -> int:
    return d.db.scalar(select(func.count()).select_from(model))


def test_tanpa_kasus_yang_sama_ada_peringatan_tanpa_garis_dan_tanpa_run_ulang(dunia_tren):
    """Edge: AC11, dua sesi di satu garis yang tidak punya satu pun kasus test yang sama
    mendapat peringatan, tanpa nilai yang digambar (bukan nol). Tren hanya membaca hasil
    tersimpan: tidak ada percobaan, panggilan provider, atau run penilaian baru."""
    # Arrange: bulan lalu hanya TRN-001 yang terjawab, bulan ini hanya TRN-002.
    sesi_otomatis(
        dunia_tren, "AiYU", kapan=waktu_di_bulan(1), provider=ProviderTren(gagal=["TRN-002"])
    )
    sesi_otomatis(
        dunia_tren, "AiYU", kapan=waktu_di_bulan(0), provider=ProviderTren(gagal=["TRN-001"])
    )
    sebelum = {m: _jumlah(dunia_tren, m) for m in (Attempt, CallLog, GradingRun)}

    # Act
    respons = tren(dunia_tren, "AiYU")

    # Assert
    assert respons.json()["warnings"] == ["NO_SHARED_CASES"]
    assert [(p["value"], p["case_count"]) for p in titik(respons)] == [(None, 0), (None, 0)]
    assert {m: _jumlah(dunia_tren, m) for m in sebelum} == sebelum
