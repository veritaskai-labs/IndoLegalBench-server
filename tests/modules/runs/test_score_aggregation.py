"""Agregasi skor percobaan, kasus, dan produk. PBI-14 AC3, AC7, PBI-11 AC9. SCRUM-225.

Yang dijaga di sini:
- Skor percobaan adalah rata-rata berbobot aspek rubrik v1, tetap di skala rubrik,
  bukan persen dan bukan "X dari N".
- Skor kasus proporsional terhadap kriteria, dan pengulangan dirata-ratakan.
- Skor produk rata-rata sederhana semua kasus dengan bobot sama.
- Jalur deterministik dan jalur judge tidak pernah dijumlahkan.
- Tiga skor dev, test, dan gabungan dihitung, bukan disimpan.
- Kasus tanpa kategori masuk "Lain-lain" dan tetap dihitung.
- Menilai ulang menandai hasil terbaru tanpa membuang hasil lama, dan hasil lama
  tetap memegang versi rubriknya sendiri.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.cases.models import SplitTag
from app.modules.runs.score_aggregation import (
    LAIN_LAIN,
    RUBRIC_V1,
    CaseScore,
    GradingResult,
    RubricAspect,
    RubricVersion,
    ScorePair,
    attempt_score,
    category_breakdown,
    criteria_score,
    latest_results,
    mark_latest,
    mean_score,
    needs_regrade,
    product_score,
    repetition_score,
    three_scores,
)

SEMPURNA = {
    RubricAspect.KESIMPULAN: 5,
    RubricAspect.DASAR_HUKUM: 5,
    RubricAspect.ARGUMENTASI: 5,
}


def kasus(
    *,
    code: str = "PJK-001",
    split: SplitTag = SplitTag.DEV,
    category: str | None = "ketenagakerjaan",
    deterministic: Decimal | None = Decimal("0.80"),
    judge: Decimal | None = Decimal("4.00"),
) -> CaseScore:
    return CaseScore(
        case_code=code,
        split_tag=split,
        category=category,
        scores=ScorePair(deterministic=deterministic, judge=judge),
    )


def hasil(
    *,
    attempt: uuid.UUID,
    versi: str = "v1",
    jam: int = 10,
    judge: Decimal | None = Decimal("4.00"),
) -> GradingResult:
    return GradingResult(
        attempt_id=attempt,
        rubric_version=versi,
        graded_at=datetime(2026, 11, 6, jam, 0, tzinfo=UTC),
        scores=ScorePair(deterministic=Decimal("0.80"), judge=judge),
    )


# --- Skor percobaan dari rubrik -------------------------------------------


def test_tiga_aspek_sempurna_jadi_nilai_tertinggi_rubrik():
    """Positif: semua aspek 5 menghasilkan 5.00, bukan 100 dan bukan 1.00."""
    # Act
    hasil_skor = attempt_score(SEMPURNA)

    # Assert
    assert hasil_skor == Decimal("5.00")


def test_aspek_berbeda_dirata_ratakan_dengan_bobot_sama():
    """Positif: 5, 4, dan 3 dengan bobot sama jadi 4.00."""
    # Arrange
    aspek = {
        RubricAspect.KESIMPULAN: 5,
        RubricAspect.DASAR_HUKUM: 4,
        RubricAspect.ARGUMENTASI: 3,
    }

    # Act
    skor = attempt_score(aspek)

    # Assert
    assert skor == Decimal("4.00")


def test_rata_rata_tidak_bulat_dibulatkan_dua_digit():
    """Edge: 5, 4, 4 menghasilkan 4.333... dan dipotong jadi dua digit."""
    # Arrange
    aspek = {
        RubricAspect.KESIMPULAN: 5,
        RubricAspect.DASAR_HUKUM: 4,
        RubricAspect.ARGUMENTASI: 4,
    }

    # Act
    skor = attempt_score(aspek)

    # Assert
    assert skor == Decimal("4.33")


def test_jawaban_fatal_bernilai_nol_bukan_tidak_dinilai():
    """Edge: nol itu angka hasil penilaian, bukan tanda tidak ada hasil."""
    # Arrange
    aspek = dict.fromkeys(RubricAspect, 0)

    # Act
    skor = attempt_score(aspek)

    # Assert
    assert skor == Decimal("0.00")


def test_bobot_rubrik_lain_mengubah_hasilnya():
    """Positif: rubrik v2 dengan bobot berbeda dihitung tanpa mengubah fungsi ini."""
    # Arrange: dasar hukum dibobot dua kali lipat
    v2 = RubricVersion(
        version="v2",
        max_aspect_score=Decimal(5),
        weights={
            RubricAspect.KESIMPULAN: Decimal(1),
            RubricAspect.DASAR_HUKUM: Decimal(2),
            RubricAspect.ARGUMENTASI: Decimal(1),
        },
    )
    aspek = {
        RubricAspect.KESIMPULAN: 5,
        RubricAspect.DASAR_HUKUM: 3,
        RubricAspect.ARGUMENTASI: 5,
    }

    # Act
    skor = attempt_score(aspek, v2)

    # Assert: (5 + 3*2 + 5) / 4
    assert skor == Decimal("4.00")


def test_aspek_yang_belum_dinilai_ditolak():
    """Negatif: skor separuh terisi lebih berbahaya daripada kesalahan yang terlihat."""
    # Act / Assert
    with pytest.raises(ValueError) as error:
        attempt_score({RubricAspect.KESIMPULAN: 5})
    assert "argumentasi" in str(error.value)
    assert "dasar_hukum" in str(error.value)


@pytest.mark.parametrize("nilai", [-1, 6])
def test_nilai_di_luar_skala_rubrik_ditolak(nilai):
    """Negatif: rubrik v1 hanya mengenal 0 sampai 5."""
    # Arrange
    aspek = {**SEMPURNA, RubricAspect.DASAR_HUKUM: nilai}

    # Act / Assert
    with pytest.raises(ValueError) as error:
        attempt_score(aspek)
    assert "dasar_hukum" in str(error.value)


# --- Skor kasus dari kriteria ---------------------------------------------


def test_tiga_dari_empat_kriteria_jadi_proporsinya():
    """Positif: contoh di tiket, 3 dari 4, keluar sebagai 0.75."""
    # Act / Assert
    assert criteria_score(3, 4) == Decimal("0.75")


def test_proporsi_berulang_dibulatkan_dua_digit():
    """Edge: 2 dari 3 adalah 0.666... dan keluar 0.67."""
    # Act / Assert
    assert criteria_score(2, 3) == Decimal("0.67")


def test_kasus_tanpa_kriteria_tidak_bisa_dinilai():
    """Edge: tidak ada kriteria berarti tidak ada angka, bukan nol."""
    # Act / Assert
    assert criteria_score(0, 0) is None


def test_kriteria_terpenuhi_melebihi_jumlahnya_ditolak():
    """Negatif: angka yang mustahil menandakan pemanggilnya salah hitung."""
    # Act / Assert
    with pytest.raises(ValueError):
        criteria_score(5, 4)


# --- Pengulangan dan rata-rata --------------------------------------------


def test_pengulangan_dirata_ratakan():
    """Positif: usulan tiket, rata-rata skor percobaan."""
    # Act
    skor = repetition_score([Decimal("4.00"), Decimal("5.00"), Decimal("3.00")])

    # Assert
    assert skor == Decimal("4.00")


def test_pengulangan_yang_gagal_tidak_menarik_rata_rata_ke_bawah():
    """Edge: percobaan tanpa angka diabaikan, bukan dihitung sebagai nol."""
    # Act
    skor = repetition_score([Decimal("4.00"), None, Decimal("5.00")])

    # Assert
    assert skor == Decimal("4.50")


def test_semua_pengulangan_gagal_berarti_tidak_ada_angka():
    """Edge: tidak ada satu pun hasil, jadi None, bukan 0.00."""
    # Act / Assert
    assert repetition_score([None, None]) is None
    assert mean_score([]) is None


# --- Skor produk dan dua jalur yang terpisah ------------------------------


def test_skor_produk_rata_rata_semua_kasus_dengan_bobot_sama():
    """Positif: kasus sulit dan kasus mudah dihitung sama rata (AC3)."""
    # Arrange
    daftar = [
        kasus(code="A", deterministic=Decimal("1.00"), judge=Decimal("5.00")),
        kasus(code="B", deterministic=Decimal("0.50"), judge=Decimal("3.00")),
    ]

    # Act
    skor = product_score(daftar)

    # Assert
    assert skor == ScorePair(deterministic=Decimal("0.75"), judge=Decimal("4.00"))


def test_dua_jalur_tidak_pernah_dijumlahkan():
    """Positif: satu jalur kosong tidak membuat jalur lain ikut kosong atau tercampur."""
    # Arrange
    daftar = [
        kasus(code="A", deterministic=Decimal("0.60"), judge=None),
        kasus(code="B", deterministic=Decimal("0.80"), judge=Decimal("4.00")),
    ]

    # Act
    skor = product_score(daftar)

    # Assert
    assert skor.deterministic == Decimal("0.70")
    assert skor.judge == Decimal("4.00")


# --- Tiga skor: dev, test, gabungan ---------------------------------------


def test_tiga_skor_dihitung_terpisah_untuk_dev_dan_test():
    """Positif: PBI-11 AC9, tiga angka dari satu daftar kasus."""
    # Arrange
    daftar = [
        kasus(code="D1", split=SplitTag.DEV, judge=Decimal("5.00")),
        kasus(code="D2", split=SplitTag.DEV, judge=Decimal("3.00")),
        kasus(code="T1", split=SplitTag.TEST, judge=Decimal("2.00")),
    ]

    # Act
    tiga = three_scores(daftar)

    # Assert
    assert tiga.dev.judge == Decimal("4.00")
    assert tiga.test.judge == Decimal("2.00")


def test_gabungan_dihitung_dari_semua_kasus_bukan_dari_dua_rata_rata():
    """Edge: komposisi dev dan test yang timpang tidak disamakan bobotnya."""
    # Arrange: dua dev bernilai 5 dan satu test bernilai 2
    daftar = [
        kasus(code="D1", split=SplitTag.DEV, judge=Decimal("5.00")),
        kasus(code="D2", split=SplitTag.DEV, judge=Decimal("5.00")),
        kasus(code="T1", split=SplitTag.TEST, judge=Decimal("2.00")),
    ]

    # Act
    tiga = three_scores(daftar)

    # Assert: 12 / 3, bukan (5 + 2) / 2
    assert tiga.combined.judge == Decimal("4.00")


def test_sesi_tanpa_kasus_test_tidak_mengarang_angka():
    """Edge: tidak ada kasus test berarti skornya None, bukan 0.00."""
    # Arrange
    daftar = [kasus(code="D1", split=SplitTag.DEV)]

    # Act
    tiga = three_scores(daftar)

    # Assert
    assert tiga.test == ScorePair(deterministic=None, judge=None)
    assert tiga.dev.judge == Decimal("4.00")


# --- Pecahan per kategori -------------------------------------------------


def test_kasus_dikelompokkan_per_kategori_snapshot():
    """Positif: tiap kategori punya angkanya sendiri dan jumlah kasusnya."""
    # Arrange
    daftar = [
        kasus(code="A", category="ketenagakerjaan", judge=Decimal("5.00")),
        kasus(code="B", category="ketenagakerjaan", judge=Decimal("3.00")),
        kasus(code="C", category="pajak", judge=Decimal("2.00")),
    ]

    # Act
    pecahan = category_breakdown(daftar)

    # Assert
    assert [(bagian.category, bagian.case_count) for bagian in pecahan] == [
        ("ketenagakerjaan", 2),
        ("pajak", 1),
    ]
    assert pecahan[0].scores.judge == Decimal("4.00")


@pytest.mark.parametrize("kosong", [None, "", "   "])
def test_kasus_tanpa_kategori_masuk_lain_lain_dan_tetap_dihitung(kosong):
    """Edge: kategori kosong tetap dinilai, tidak dibuang (PBI-14 AC10)."""
    # Arrange
    daftar = [kasus(code="A", category=kosong, judge=Decimal("3.00"))]

    # Act
    pecahan = category_breakdown(daftar)

    # Assert
    assert pecahan[0].category == LAIN_LAIN
    assert pecahan[0].case_count == 1
    assert pecahan[0].scores.judge == Decimal("3.00")


def test_lain_lain_berada_di_akhir_daftar():
    """Edge: sisa yang tidak berkategori tidak menyela kategori bernama."""
    # Arrange
    daftar = [
        kasus(code="A", category=None),
        kasus(code="B", category="pajak"),
        kasus(code="C", category="aset"),
    ]

    # Act
    pecahan = category_breakdown(daftar)

    # Assert
    assert [bagian.category for bagian in pecahan] == ["aset", "pajak", LAIN_LAIN]


# --- Menilai ulang dan riwayatnya -----------------------------------------


def test_hasil_terbaru_ditandai_dan_hasil_lama_tetap_ada():
    """Positif: AC7, menilai ulang tidak menghapus apa pun."""
    # Arrange
    percobaan = uuid.uuid4()
    lama = hasil(attempt=percobaan, versi="v1", jam=9)
    baru = hasil(attempt=percobaan, versi="v2", jam=11)

    # Act
    catatan = mark_latest([lama, baru])

    # Assert
    assert len(catatan) == 2
    assert catatan[0].is_latest is False
    assert catatan[1].is_latest is True


def test_hasil_lama_tetap_memegang_versi_rubriknya_sendiri():
    """Positif: angka lama tidak ikut berubah saat rubrik baru dipakai."""
    # Arrange
    percobaan = uuid.uuid4()
    lama = hasil(attempt=percobaan, versi="v1", jam=9, judge=Decimal("3.00"))
    baru = hasil(attempt=percobaan, versi="v2", jam=11, judge=Decimal("4.00"))

    # Act
    catatan = mark_latest([lama, baru])

    # Assert
    assert catatan[0].result.rubric_version == "v1"
    assert catatan[0].result.scores.judge == Decimal("3.00")


def test_setiap_percobaan_punya_satu_hasil_terbaru():
    """Positif: dua percobaan berbeda masing-masing menyumbang satu angka."""
    # Arrange
    satu, dua = uuid.uuid4(), uuid.uuid4()
    daftar = [
        hasil(attempt=satu, jam=9, judge=Decimal("3.00")),
        hasil(attempt=satu, jam=11, judge=Decimal("5.00")),
        hasil(attempt=dua, jam=10, judge=Decimal("4.00")),
    ]

    # Act
    terbaru = latest_results(daftar)

    # Assert
    assert len(terbaru) == 2
    assert sorted(item.scores.judge for item in terbaru) == [Decimal("4.00"), Decimal("5.00")]


def test_dua_hasil_berwaktu_sama_dimenangkan_yang_belakangan_masuk():
    """Edge: penilaian ulang dalam detik yang sama tetap punya satu pemenang."""
    # Arrange
    percobaan = uuid.uuid4()
    pertama = hasil(attempt=percobaan, versi="v1", jam=10, judge=Decimal("3.00"))
    kedua = hasil(attempt=percobaan, versi="v2", jam=10, judge=Decimal("5.00"))

    # Act
    terbaru = latest_results([pertama, kedua])

    # Assert
    assert len(terbaru) == 1
    assert terbaru[0].rubric_version == "v2"


def test_percobaan_dengan_rubrik_lama_terdaftar_untuk_dinilai_ulang():
    """Positif: AC7, yang perlu dinilai ulang bisa ditemukan tanpa menyentuh database."""
    # Arrange
    lama, baru = uuid.uuid4(), uuid.uuid4()
    daftar = [
        hasil(attempt=lama, versi="v0", jam=9),
        hasil(attempt=baru, versi=RUBRIC_V1.version, jam=9),
    ]

    # Act
    perlu = needs_regrade(daftar)

    # Assert
    assert perlu == (lama,)


def test_percobaan_yang_sudah_dinilai_ulang_tidak_terdaftar_lagi():
    """Edge: hasil lama berubah rubrik tidak membuatnya muncul terus."""
    # Arrange
    percobaan = uuid.uuid4()
    daftar = [
        hasil(attempt=percobaan, versi="v0", jam=9),
        hasil(attempt=percobaan, versi=RUBRIC_V1.version, jam=11),
    ]

    # Act / Assert
    assert needs_regrade(daftar) == ()
