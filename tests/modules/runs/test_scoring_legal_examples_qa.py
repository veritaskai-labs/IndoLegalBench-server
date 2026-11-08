"""QA jalur deterministik dengan contoh rujukan hukum. PBI-14 (SCRUM-78) AC1, AC2; SCRUM-229.

Membuktikan jalur deterministik membaca rujukan sesuai acuan tim legal: bentuk singkat
dan bentuk panjang satu peraturan dinilai sama, ayat terbaca, nama populer tanpa nomor
dan tahun tidak dihitung, rangkaian juncto harus lengkap, pasal di luar acuan tidak
menghukum, dan jawaban yang sama selalu menghasilkan angka yang sama.

Teks jawaban di sini dikarang untuk menguji bentuk rujukannya, bukan sebagai pendapat
hukum, sama seperti tests/citation_examples.py.

Label: Positif = dinilai benar, Negatif = dinilai salah, Edge = batas perilaku.
"""

from decimal import Decimal

from app.modules.legal_refs.seed import load_regulation_types
from app.modules.legal_refs.service import TypeName
from app.modules.runs import citation_score
from tests.citation_examples import link, rujukan


def _nilai(jawaban: str, acuan: list[dict]):
    jenis = {item.code: TypeName(item.label, item.singkatan) for item in load_regulation_types()}
    return citation_score.CitationScorer(jenis).score(jawaban, acuan)


# Bentuk rujukan yang sama dinilai sama


def test_bentuk_singkat_dan_panjang_satu_peraturan_dinilai_sama():
    """Positif: 'UU 40/2007' sama dengan 'Undang-Undang Nomor 40 Tahun 2007' (AC2)."""
    acuan = [rujukan(link("uu", "40", 2007))]

    singkat = _nilai("Kewajiban direksi diatur UU 40/2007.", acuan)
    panjang = _nilai(
        "Kewajiban direksi diatur Undang-Undang Nomor 40 Tahun 2007 tentang Perseroan Terbatas.",
        acuan,
    )

    assert singkat.score == panjang.score == Decimal("1.00")
    assert singkat.regulation_matches[0].matched is True
    assert panjang.regulation_matches[0].matched is True


def test_pasal_dengan_ayat_dalam_kurung_terbaca():
    """Positif: 'Pasal 156 ayat (1)' dihitung sebagai pasal 156 yang disebut (AC2)."""
    acuan = [rujukan(link("uu", "13", 2003, "156"))]

    hasil = _nilai(
        "Uang pesangon diatur Pasal 156 ayat (1) Undang-Undang Nomor 13 Tahun 2003.", acuan
    )

    assert hasil.score == Decimal("1.00")
    assert hasil.article_matches[0].cited is True


# Rujukan yang tidak lengkap dinilai salah


def test_nama_populer_tanpa_nomor_dan_tahun_tidak_dihitung():
    """Negatif: menyebut nama undang-undangnya saja bukan rujukan yang benar (AC2)."""
    acuan = [rujukan(link("uu", "40", 2007))]

    hasil = _nilai(
        "Kewajiban direksi diatur dalam Undang-Undang Perseroan Terbatas.",
        acuan,
    )

    assert hasil.score == Decimal("0.00")
    assert hasil.regulation_matches[0].matched is False


def test_rangkaian_juncto_lengkap_dinilai_benar():
    acuan = [
        rujukan(
            link("uu", "8", 1999, "19"),
            link("uu", "7", 2014, "65", connector="juncto"),
        )
    ]

    jawaban = (
        "Ganti rugi diatur dalam Pasal 19 UU Nomor 8 Tahun 1999 jo. Pasal 65 UU Nomor 7 Tahun 2014."
    )

    hasil = _nilai(jawaban, acuan)

    assert hasil.all_chained_cited is True
    assert hasil.score == Decimal("1.00")


# Batas perilaku


def test_jawaban_yang_sama_dinilai_dua_kali_memberi_angka_identik():
    """Edge: jalur deterministik tidak memakai keacakan, jadi hasilnya stabil (AC1)."""
    acuan = [rujukan(link("uu", "8", 1999, "7")), rujukan(link("pp", "80", 2019))]
    jawaban = (
        "Penjual wajib memberi informasi benar sesuai Pasal 7 UU Nomor 8 Tahun 1999, "
        "dan menyimpan bukti transaksi menurut PP 80/2019."
    )

    pertama = _nilai(jawaban, acuan)
    kedua = _nilai(jawaban, acuan)

    assert pertama == kedua


def test_pasal_di_luar_acuan_tidak_menurunkan_skor():
    """Edge: sitasi tambahan di luar acuan tidak dihukum (AC2)."""
    acuan = [rujukan(link("uu", "13", 2003, "156"))]

    hasil = _nilai(
        "Pesangon diatur Pasal 156 Undang-Undang Nomor 13 Tahun 2003, "
        "dan Pasal 61 undang-undang yang sama mengatur berakhirnya hubungan kerja.",
        acuan,
    )

    assert hasil.score == Decimal("1.00")
    assert hasil.reference_correct == hasil.reference_total


def test_rujukan_historis_tetap_dinilai_dari_acuan_kasus():
    """Edge: penilaian mengikuti rujukan acuan kasus tanpa memeriksa status pencabutan."""
    acuan = [rujukan(link("uu", "11", 2008, "5"))]

    hasil = _nilai(
        "Bukti elektronik sah sebagai alat bukti menurut Pasal 5 UU Nomor 11 Tahun 2008.",
        acuan,
    )

    assert hasil.score == Decimal("1.00")
    assert hasil.article_matches[0].cited is True
