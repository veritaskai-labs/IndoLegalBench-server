"""Jalur deterministik: pencocokan peraturan dari jawaban model. PBI-14 AC1, AC2.

Rujukan peraturan diekstrak dari jawaban lalu dicocokkan dengan rujukan acuan
kasus (legal_refs bentuk PBI-21 di snapshot). Benar bila jenis, nomor, dan tahun
sama; tanpa daftar sinonim, jadi nama populer tanpa nomor dan tahun salah.
Jenis dikenali dari label dan singkatan di regulation_types, sama seperti
penulisan display_text (SCRUM-160).

Skor = rujukan acuan yang disebut benar / rujukan acuan yang bisa dinilai
(recall) secara default. Rumus klien "total extracted sitasi / total real sitasi
benar" belum dikonfirmasi (TBA AC1), jadi rumus bisa diganti ke presisi.
Menilai ulang jawaban yang sama selalu memberi hasil yang sama persis.

Contoh hukum: UU 40/2007 dari tiket PBI-14, KTK-001 dan AC10 (tests/legal_refs.py).
Nilai lain input format sintetis.

Label: Positif = dinilai benar, Negatif = dinilai salah, Edge = batas perilaku.
"""

from decimal import Decimal

import pytest

from app.modules.legal_refs.seed import load_regulation_types
from app.modules.legal_refs.service import TypeName
from app.modules.runs import citation_score
from tests.legal_refs import rujukan_ktk_001

# Baris dari seed regulation_types 2026-10-16 yang dipakai test ini.
JENIS = {
    "uu": TypeName("Undang-undang", "UU"),
    "perpu": TypeName("Peraturan Pemerintah Pengganti Undang-Undang", "Perpu"),
    "pp": TypeName("Peraturan Pemerintah", "PP"),
    "peraturan_otoritas_jasa_keuangan": TypeName("Peraturan Otoritas Jasa Keuangan", None),
}


def _uu_40_2007() -> dict:
    """Contoh tiket PBI-14: UU 40/2007 (Perseroan Terbatas), tanpa pasal."""
    return _nama("uu", "40", 2007)


def _nama(regulation_type: str, number: str, year: int | None) -> dict:
    """Rujukan structured berbentuk nama peraturan saja (name_only)."""
    return {
        "kind": "structured",
        "links": [
            {
                "form": "name_only",
                "connector": "none",
                "regulation_type": regulation_type,
                "number": number,
                "year": year,
                "pasal": None,
            }
        ],
    }


def _nilai(
    jawaban: str,
    acuan: list[dict],
    formula: citation_score.ScoreFormula = citation_score.ScoreFormula.RECALL,
):
    return citation_score.CitationScorer(JENIS, formula=formula).score(jawaban, acuan)


@pytest.mark.parametrize(
    "jawaban",
    [
        "Perseroan tunduk pada UU 40/2007.",
        "Perseroan tunduk pada Undang-Undang Nomor 40 Tahun 2007 tentang Perseroan Terbatas.",
        "Perseroan tunduk pada UU No. 40 Tahun 2007.",
        "Perseroan tunduk pada Undang-Undang Republik Indonesia Nomor 40 Tahun 2007.",
        "perseroan tunduk pada undang-undang nomor 40 tahun 2007.",
        "Perseroan tunduk pada Undang Undang 40 Tahun 2007.",
    ],
)
def test_penulisan_singkat_dan_lengkap_sama_benarnya(jawaban):
    """Positif: AC2, 'UU 40/2007' sama dengan 'Undang-Undang Nomor 40 Tahun 2007'."""
    # Act
    hasil = _nilai(jawaban, [_uu_40_2007()])

    # Assert
    assert hasil.score == Decimal("1.00")
    assert [(m.regulation_type, m.number, m.year, m.matched) for m in hasil.regulation_matches] == [
        ("uu", "40", 2007, True)
    ]


def test_nama_populer_tanpa_nomor_dan_tahun_dianggap_salah():
    """Negatif: AC2, 'UU Perseroan Terbatas' tidak bisa dipastikan peraturannya."""
    # Act
    hasil = _nilai("Perseroan tunduk pada UU Perseroan Terbatas.", [_uu_40_2007()])

    # Assert
    assert hasil.score == Decimal("0.00")
    assert hasil.extracted_total == 0
    assert hasil.regulation_matches[0].matched is False


@pytest.mark.parametrize(
    "jawaban",
    [
        "Perseroan tunduk pada UU 40/2008.",
        "Perseroan tunduk pada UU 41/2007.",
        "Perseroan tunduk pada PP 40/2007.",
        "Perseroan tunduk pada Undang-Undang Nomor 40.",
    ],
)
def test_jenis_nomor_atau_tahun_yang_berbeda_dianggap_salah(jawaban):
    """Negatif: AC2, ketiganya wajib sama; nomor tanpa tahun juga tidak cukup."""
    # Act
    hasil = _nilai(jawaban, [_uu_40_2007()])

    # Assert
    assert hasil.score == Decimal("0.00")
    assert hasil.regulation_matches[0].matched is False


def test_jenis_terpanjang_yang_dipakai_bukan_bagian_namanya():
    """Edge: 'Peraturan Pemerintah Pengganti Undang-Undang' adalah Perpu, bukan UU atau PP."""
    # Arrange
    jawaban = "Lihat Peraturan Pemerintah Pengganti Undang-Undang Nomor 2 Tahun 2022."

    # Act
    hasil = _nilai(jawaban, [_nama("uu", "2", 2022), _nama("perpu", "2", 2022)])

    # Assert
    assert [m.matched for m in hasil.regulation_matches] == [False, True]
    assert hasil.extracted_total == 1


def test_nomor_yang_memuat_tahun_cocok_dengan_acuan():
    """Positif: nomor seperti '12/POJK.03/2018' sudah memuat tahun, tidak ditulis ulang."""
    # Arrange
    acuan = [_nama("peraturan_otoritas_jasa_keuangan", "12/POJK.03/2018", 2018)]
    jawaban = "Bank wajib mengikuti Peraturan Otoritas Jasa Keuangan Nomor 12/POJK.03/2018."

    # Act
    hasil = _nilai(jawaban, acuan)

    # Assert
    assert hasil.score == Decimal("1.00")


def test_rujukan_tambahan_tidak_menurunkan_skor_recall():
    """Positif: AC2, peraturan tambahan tidak dihukum; dua acuan, satu disebut = 0.50."""
    # Arrange
    acuan = [rujukan_ktk_001(), _uu_40_2007()]
    jawaban = "Pesangon diatur Pasal 156 UU 13/2003. Lihat juga PP 35/2021 dan PP 36/2021."

    # Act
    hasil = _nilai(jawaban, acuan)

    # Assert
    assert hasil.score == Decimal("0.50")
    assert (hasil.reference_correct, hasil.reference_total) == (1, 2)
    assert (hasil.extracted_correct, hasil.extracted_total) == (1, 3)


def test_rumus_presisi_bisa_dipilih_selama_rumus_klien_tba():
    """Positif: AC1 TBA, presisi = sitasi terekstrak yang benar / sitasi terekstrak."""
    # Arrange
    acuan = [rujukan_ktk_001(), _uu_40_2007()]
    jawaban = "Pesangon diatur Pasal 156 UU 13/2003. Lihat juga PP 35/2021 dan PP 36/2021."

    # Act
    hasil = _nilai(jawaban, acuan, citation_score.ScoreFormula.PRECISION)

    # Assert
    assert hasil.score == Decimal("0.33")
    assert hasil.formula is citation_score.ScoreFormula.PRECISION


def test_sitasi_yang_diulang_dihitung_sekali():
    """Edge: menyebut UU 13/2003 tiga kali tetap satu sitasi terekstrak."""
    # Act
    hasil = _nilai("UU 13/2003, UU 13/2003, dan UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert (hasil.extracted_correct, hasil.extracted_total) == (1, 1)


def test_skor_dua_digit_dibulatkan_setengah_ke_atas():
    """Edge: AC3, 2 dari 3 acuan = 0.67, Decimal dua digit, bukan '2 dari 3' atau persen."""
    # Arrange
    acuan = [_nama("uu", "1", 2001), _nama("uu", "2", 2002), _nama("uu", "3", 2003)]

    # Act
    hasil = _nilai("Lihat UU 1/2001 dan UU 2/2002.", acuan)

    # Assert
    assert hasil.score == Decimal("0.67")


def test_teks_bebas_tidak_bisa_dinilai_dan_tidak_masuk_penyebut():
    """Edge: rujukan teks bebas (mis. 'Kitab Undang-Undang Hukum Perdata') tanpa jenis/nomor/tahun."""
    # Arrange
    teks_bebas = {"kind": "free_text", "free_text": "Kitab Undang-Undang Hukum Perdata"}

    # Act
    hasil = _nilai("Pesangon diatur Pasal 156 UU 13/2003.", [rujukan_ktk_001(), teks_bebas])

    # Assert
    assert hasil.score == Decimal("1.00")
    assert hasil.reference_total == 1
    assert hasil.ungradable_refs == ("Kitab Undang-Undang Hukum Perdata",)


def test_link_lama_tanpa_tahun_tidak_bisa_dinilai():
    """Edge: link Sprint 1 boleh tanpa tahun (PBI-21 AC9); tiga aturan dasar tidak bisa dipakai."""
    # Act
    hasil = _nilai("Lihat UU 13/2003.", [_nama("uu", "13", None)])

    # Assert
    assert hasil.score is None
    assert hasil.reference_total == 0
    assert hasil.ungradable_refs == ("UU 13",)


def test_tanpa_rujukan_acuan_tidak_ada_skor_deterministik():
    """Edge: kasus tanpa rujukan yang bisa dinilai mendapat skor kosong, bukan 0 atau 1."""
    # Act
    hasil = _nilai("Pesangon diatur UU 13/2003.", [])

    # Assert
    assert hasil.score is None
    assert hasil.regulation_matches == ()


def test_jawaban_kosong_bernilai_nol():
    """Negatif: tidak ada sitasi sama sekali, semua acuan tidak disebut."""
    # Act
    hasil = _nilai("", [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("0.00")
    assert hasil.extracted_total == 0


def test_menilai_ulang_jawaban_yang_sama_hasilnya_sama_persis():
    """Positif: AC1 reproducible, penilai baru dan urutan acuan sama memberi hasil identik."""
    # Arrange
    acuan = [rujukan_ktk_001(), _uu_40_2007(), _nama("pp", "35", 2021)]
    jawaban = "Pesangon: Pasal 156 UU 13/2003 dan PP 35/2021. Perseroan: UU Perseroan Terbatas."

    # Act
    pertama = _nilai(jawaban, acuan)
    kedua = citation_score.CitationScorer(dict(reversed(list(JENIS.items())))).score(jawaban, acuan)

    # Assert
    assert pertama == kedua
    assert pertama.score == Decimal("0.67")


def test_seluruh_jenis_di_seed_bisa_dipakai():
    """Edge: penilai dibangun dari 266 jenis seed; nama panjang tidak menelan singkatan pendek."""
    # Arrange
    jenis = {item.code: TypeName(item.label, item.singkatan) for item in load_regulation_types()}
    acuan = [rujukan_ktk_001(), _nama("perpu", "2", 2022)]
    jawaban = "Lihat Pasal 156 UU 13/2003 dan Peraturan Pemerintah Pengganti Undang-Undang Nomor 2 Tahun 2022."

    # Act
    hasil = citation_score.CitationScorer(jenis).score(jawaban, acuan)

    # Assert
    assert hasil.score == Decimal("1.00")
    assert hasil.extracted_total == 2


def test_presisi_tanpa_sitasi_terekstrak_bernilai_nol():
    """Edge: presisi 0/0 saat jawaban tidak menyebut apa pun dihitung 0.00, bukan kosong."""
    # Act
    hasil = _nilai("Tidak ada dasar hukum.", [_uu_40_2007()], citation_score.ScoreFormula.PRECISION)

    # Assert
    assert hasil.score == Decimal("0.00")


def test_jenis_yang_tidak_ada_di_daftar_ditulis_dengan_kodenya():
    """Edge: link lama dengan jenis di luar daftar tetap tercatat, memakai code-nya."""
    # Act
    hasil = _nilai("Lihat UU 13/2003.", [_nama("jenis_lama", "7", None)])

    # Assert
    assert hasil.ungradable_refs == ("jenis_lama 7",)
