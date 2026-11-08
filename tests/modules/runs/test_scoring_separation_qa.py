"""QA pemisahan jalur dan tiga skor. PBI-14 (SCRUM-78) AC1, AC3; PBI-11 AC9; SCRUM-229.

Membuktikan skor deterministik dan skor LLM judge tidak pernah digabung jadi satu angka,
tiga skor dev/test/gabungan dihitung terpisah, dan kasus tanpa kategori masuk "Lain-lain"
tetapi tetap dihitung.

Pengurangan skor karena jebakan tidak diuji di sini: field itu dihapus klien dan sudah
dijaga tes SCRUM-256 (tests/modules/cases/test_sections_without_traps.py).

Label: Positif = perilaku yang diharapkan, Negatif = harus ditolak, Edge = batas perilaku.
"""

import uuid
from decimal import Decimal

from app.modules.cases.models import SplitTag
from app.modules.runs.schemas import CaseGradeResult
from app.modules.runs.schemas import ScorePair as ScorePairKontrak
from app.modules.runs.score_aggregation import (
    LAIN_LAIN,
    CaseScore,
    ScorePair,
    category_breakdown,
    pair_mean,
    product_score,
    three_scores,
)
from app.modules.runs.session_grade import compute_category_scores, compute_three_scores


def _kasus_hasil(code: str, split: str, *, det: str, kualitas: str, format_: str, category=None):
    """Satu baris hasil seperti yang dibentuk endpoint Nilai, dengan ScorePair tiga field."""
    return CaseGradeResult(
        snapshot_item_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        case_code=code,
        title=f"Judul {code}",
        split_tag=split,
        category=category,
        scores=ScorePairKontrak(
            deterministic=Decimal(det), quality=Decimal(kualitas), format=Decimal(format_)
        ),
    )


def _kasus(
    code: str,
    split: SplitTag,
    *,
    det: str | None = None,
    judge: str | None = None,
    category: str | None = None,
) -> CaseScore:
    return CaseScore(
        case_code=code,
        split_tag=split,
        category=category,
        scores=ScorePair(
            deterministic=Decimal(det) if det is not None else None,
            judge=Decimal(judge) if judge is not None else None,
        ),
    )


# Dua jalur tidak pernah digabung


def test_rata_rata_menghitung_tiap_jalur_sendiri_sendiri():
    """Positif: deterministik dan judge dirata-rata terpisah, bukan dicampur (AC1)."""
    kasus = [
        _kasus("A", SplitTag.DEV, det="1.00", judge="0.00"),
        _kasus("B", SplitTag.DEV, det="0.00", judge="1.00"),
    ]

    hasil = pair_mean(kasus)

    assert hasil == ScorePair(deterministic=Decimal("0.50"), judge=Decimal("0.50"))


def test_jalur_tanpa_skor_tetap_kosong_bukan_nol():
    """Negatif: jalur yang belum dinilai tidak boleh muncul sebagai 0.00 (AC1)."""
    kasus = [_kasus("A", SplitTag.DEV, det="0.80", judge=None)]

    hasil = product_score(kasus)

    assert hasil.deterministic == Decimal("0.80")
    assert hasil.judge is None


def test_skor_produk_tidak_pernah_menghasilkan_satu_angka_gabungan():
    """Edge: setiap tipe kembalian membungkus ScorePair, jadi angka gabungan tidak ada (AC1)."""
    kasus = [_kasus("A", SplitTag.DEV, det="1.00", judge="0.50")]

    hasil = product_score(kasus)

    assert isinstance(hasil, ScorePair)
    assert not hasattr(hasil, "combined")
    assert not hasattr(hasil, "total")


# Tiga skor terpisah


def test_tiga_skor_memisahkan_dev_test_dan_gabungan():
    """Positif: dev, test, dan gabungan masing-masing ScorePair sendiri (PBI-11 AC9)."""
    kasus = [
        _kasus("D1", SplitTag.DEV, det="1.00", judge="1.00"),
        _kasus("T1", SplitTag.TEST, det="0.00", judge="0.00"),
    ]

    hasil = three_scores(kasus)

    assert hasil.dev.deterministic == Decimal("1.00")
    assert hasil.test.deterministic == Decimal("0.00")
    assert hasil.combined.deterministic == Decimal("0.50")


def test_gabungan_adalah_rata_rata_semua_kasus_bukan_rata_rata_dua_kelompok():
    """Edge: pembagian dev dan test yang tidak seimbang tidak disamakan bobotnya (PBI-11 AC9).

    Dua kasus dev bernilai 1.00 dan satu kasus test bernilai 0.00. Rata-rata semua kasus
    0.67, sedangkan rata-rata dua rata-rata kelompok akan 0.50.
    """
    kasus = [
        _kasus("D1", SplitTag.DEV, det="1.00"),
        _kasus("D2", SplitTag.DEV, det="1.00"),
        _kasus("T1", SplitTag.TEST, det="0.00"),
    ]

    hasil = three_scores(kasus)

    assert hasil.combined.deterministic == Decimal("0.67")


def test_skor_selalu_dua_digit_desimal():
    """Positif: angka dibulatkan dua digit, tanpa persen dan tanpa format 'X dari N' (AC3)."""
    kasus = [
        _kasus("A", SplitTag.DEV, det="1.00"),
        _kasus("B", SplitTag.DEV, det="1.00"),
        _kasus("C", SplitTag.DEV, det="0.00"),
    ]

    hasil = product_score(kasus)

    assert hasil.deterministic == Decimal("0.67")
    assert str(hasil.deterministic) == "0.67"


# Pecahan per kategori


def test_kasus_tanpa_kategori_masuk_lain_lain_dan_tetap_dihitung():
    """Positif: kategori kosong tidak membuang kasusnya dari laporan (PBI-14 AC10)."""
    kasus = [
        _kasus("A", SplitTag.DEV, det="1.00", category="Ketenagakerjaan"),
        _kasus("B", SplitTag.DEV, det="0.00", category=None),
    ]

    pecahan = category_breakdown(kasus)

    per_nama = {bagian.category: bagian for bagian in pecahan}
    assert per_nama[LAIN_LAIN].case_count == 1
    assert per_nama[LAIN_LAIN].scores.deterministic == Decimal("0.00")


# Jalur yang benar-benar dilayani endpoint Nilai
#
# Test di atas memaku score_aggregation, yang tidak diimpor session_grade.py sama sekali.
# Endpoint memakai compute_category_scores dan compute_three_scores dengan ScorePair tiga
# field. Dua test berikut memaku jalur itu, supaya AC1 dan AC10 terbukti pada kode yang
# benar-benar dijalankan, bukan pada implementasi kembarnya.


def test_endpoint_memisahkan_tiga_field_skor_tanpa_angka_gabungan():
    """Positif: deterministik, kualitas dan format tetap tiga angka terpisah (AC1)."""
    kasus = [
        _kasus_hasil("A", "dev", det="1.00", kualitas="0.00", format_="0.50"),
        _kasus_hasil("B", "dev", det="0.00", kualitas="1.00", format_="0.50"),
    ]

    hasil = compute_three_scores(kasus)

    assert hasil.dev.deterministic == Decimal("0.50")
    assert hasil.dev.quality == Decimal("0.50")
    assert hasil.dev.format == Decimal("0.50")
    assert not hasattr(hasil.dev, "judge"), "kontrak tidak punya satu angka judge gabungan"


def test_endpoint_memasukkan_kasus_tanpa_kategori_ke_lain_lain():
    """Positif: kategori kosong jadi 'Lain-lain' dan kasusnya tetap dihitung (AC10).

    Urutan tidak dipaku di sini. PBI-14 tidak menyebut urutan, dan dua implementasi di
    repo ini memang berbeda: score_aggregation menaruh 'Lain-lain' paling akhir, sedangkan
    endpoint mengurut abjad biasa sehingga 'Lain-lain' bisa di tengah. Perbedaan itu
    dilaporkan sebagai temuan, bukan dikunci sebagai aturan oleh test ini.
    """
    kasus = [
        _kasus_hasil("A", "dev", det="1.00", kualitas="1.00", format_="1.00", category="Zonasi"),
        _kasus_hasil("B", "dev", det="0.00", kualitas="0.00", format_="0.00", category=None),
    ]

    pecahan = compute_category_scores(kasus)

    per_nama = {bagian.category: bagian for bagian in pecahan}
    assert per_nama[LAIN_LAIN].case_count == 1
    assert per_nama[LAIN_LAIN].scores.deterministic == Decimal("0.00")
    assert per_nama["Zonasi"].case_count == 1


def test_endpoint_gabungan_menggunakan_rata_rata_semua_kasus():
    kasus = [
        _kasus_hasil("D1", "dev", det="1.00", kualitas="5.00", format_="1.00"),
        _kasus_hasil("D2", "dev", det="1.00", kualitas="5.00", format_="1.00"),
        _kasus_hasil("T1", "test", det="0.00", kualitas="0.00", format_="0.00"),
    ]

    hasil = compute_three_scores(kasus)

    assert hasil.dev.deterministic == Decimal("1.00")
    assert hasil.test.deterministic == Decimal("0.00")
    assert hasil.combined.deterministic == Decimal("0.67")

    assert hasil.combined.quality == Decimal("3.33")
    assert hasil.combined.format == Decimal("0.67")
