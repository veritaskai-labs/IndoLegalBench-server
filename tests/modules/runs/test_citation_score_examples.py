"""Jalur deterministik pada jawaban panjang berisi beberapa kalimat. PBI-14 AC1, AC2.

Jawaban yang menyebut semua sumber acuan dengan jenis, nomor, dan tahun harus
mendapat skor penuh. Kodifikasi tanpa nomor dan tahun (KUHPerdata, HIR) tercatat
tidak bisa dinilai dan tidak masuk penyebut, menunggu keputusan PO/klien.

Teks dan sumber ada di tests/citation_examples.py (sintetis).

Label: Positif = dinilai benar, Negatif = dinilai salah, Edge = batas perilaku.
"""

from decimal import Decimal

from app.modules.legal_refs.seed import load_regulation_types
from app.modules.legal_refs.service import TypeName
from app.modules.runs import citation_score
from tests.citation_examples import (
    AKTA_NOTARIS,
    BELANJA_DARING,
    DATA_PRIBADI,
    link,
    rujukan,
    sumber_akta_notaris,
    sumber_belanja_daring,
)


def _nilai(jawaban: str, acuan: list[dict]):
    # Daftar jenis lengkap dari seed, seperti di aplikasi.
    jenis = {item.code: TypeName(item.label, item.singkatan) for item in load_regulation_types()}
    return citation_score.CitationScorer(jenis).score(jawaban, acuan)


def test_jawaban_belanja_daring_mendapat_skor_penuh():
    """Positif: 'Undang-Undang (UU) Nomor 8 Tahun 1999' dan 'PP 80/2019' terbaca benar."""
    # Act
    hasil = _nilai(BELANJA_DARING, sumber_belanja_daring())

    # Assert
    assert hasil.score == Decimal("1.00")
    assert (hasil.extracted_correct, hasil.extracted_total) == (3, 4)


def test_rangkaian_juncto_di_jawaban_belanja_daring_benar():
    """Positif: 'Pasal 19 UU 8/1999 juncto Pasal 65 UU Nomor 7 Tahun 2014' sebagai rangkaian."""
    # Arrange
    acuan = [rujukan(link("uu", "8", 1999, "19"), link("uu", "7", 2014, "65", connector="jo"))]

    # Act
    hasil = _nilai(BELANJA_DARING, acuan)

    # Assert
    assert hasil.score == Decimal("1.00")
    assert hasil.all_chained_cited is True


def test_ayat_tanpa_kurung_tetap_terbaca():
    """Positif: 'Pasal 7 ayat 1 huruf b Undang-Undang (UU) Nomor 8 Tahun 1999'."""
    # Act
    hasil = _nilai(BELANJA_DARING, [rujukan(link("uu", "8", 1999, "7"))])

    # Assert
    assert hasil.score == Decimal("1.00")


def test_kodifikasi_tidak_masuk_penyebut():
    """Edge: KUHPerdata dan HIR tidak bisa dinilai; UU 30/2004 tetap dinilai."""
    # Act
    hasil = _nilai(AKTA_NOTARIS, sumber_akta_notaris())

    # Assert
    assert hasil.score == Decimal("1.00")
    assert hasil.reference_total == 1
    assert hasil.ungradable_refs == (
        "Kitab Undang-Undang Hukum Perdata",
        "Herzien Inlandsch Reglement",
    )


def test_pasal_undang_undang_tersebut_dikaitkan_ke_uu_sebelumnya():
    """Positif: 'Pasal 15 ayat (1) undang-undang tersebut' merujuk UU 30/2004 sebelumnya."""
    # Act
    hasil = _nilai(AKTA_NOTARIS, [rujukan(link("uu", "30", 2004, "15"))])

    # Assert
    assert hasil.score == Decimal("1.00")


def test_pasal_undang_undang_yang_sama_dikaitkan_ke_uu_sebelumnya():
    """Positif: 'Pasal 46 undang-undang yang sama' merujuk UU 27/2022 sebelumnya."""
    # Act
    hasil = _nilai(DATA_PRIBADI, [rujukan(link("uu", "27", 2022, "46"))])

    # Assert
    assert hasil.score == Decimal("1.00")


def test_pasal_kuhperdata_tidak_dikaitkan_ke_uu_berikutnya():
    """Negatif: 'Pasal 1338 KUHPerdata' muncul sebelum UU 27/2022, jadi bukan pasal UU itu."""
    # Act
    hasil = _nilai(DATA_PRIBADI, [rujukan(link("uu", "27", 2022, "1338"))])

    # Assert
    assert hasil.score == Decimal("0.00")
