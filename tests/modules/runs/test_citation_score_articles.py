"""Jalur deterministik: pasal dan rujukan berangkai. PBI-14 AC2.

Rujukan acuan berpasal benar bila peraturannya cocok (jenis, nomor, tahun) dan
pasal acuannya disebut untuk peraturan itu. "Pasal 156 ayat (1)" memenuhi acuan
"Pasal 156"; pasal tambahan tidak dihukum. Pasal dikaitkan ke peraturan yang
langsung menyusulnya ("Pasal 156 ayat (1) UU 13/2003", "Pasal 155 dan 156
UU 13/2003"); bila tidak ada, ke peraturan terakhir yang disebut sebelumnya
("Pasal 46 undang-undang yang sama").

Rujukan berangkai (jo./jis.) dinilai satu kesatuan: benar hanya bila semua
peraturan di dalamnya disebut benar (D6 det_all_chained_cited). Penilai LLM
tidak strict untuk rangkaian, itu jalur SCRUM-224.

Contoh hukum: KTK-001 dan AC10 (tests/legal_refs.py), sisanya sintetis.

Label: Positif = dinilai benar, Negatif = dinilai salah, Edge = batas perilaku.
"""

import time
from decimal import Decimal

import pytest

from app.modules.legal_refs.service import TypeName
from app.modules.runs import citation_score
from tests.citation_examples import link, rujukan
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001

JENIS = {
    "uu": TypeName("Undang-undang", "UU"),
    "pp": TypeName("Peraturan Pemerintah", "PP"),
}


def _nilai(jawaban: str, acuan: list[dict]):
    return citation_score.CitationScorer(JENIS).score(jawaban, acuan)


def _pasal(hasil) -> list[tuple]:
    return [(m.regulation_type, m.number, m.year, m.pasal, m.cited) for m in hasil.article_matches]


@pytest.mark.parametrize(
    "jawaban",
    [
        "Pesangon diatur Pasal 156 UU 13/2003.",
        "Pesangon diatur Pasal 156 ayat (1) Undang-Undang Nomor 13 Tahun 2003.",
        "Pesangon diatur pasal 156 ayat 2 huruf a UU 13/2003.",
        "Pesangon diatur dalam Pasal 156 dari UU 13/2003.",
    ],
)
def test_pasal_acuan_yang_disebut_untuk_peraturannya_benar(jawaban):
    """Positif: AC2, 'Pasal 156 ayat (1)' memenuhi acuan 'Pasal 156'."""
    # Act
    hasil = _nilai(jawaban, [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("1.00")
    assert _pasal(hasil) == [("uu", "13", 2003, "156", True)]


def test_peraturan_benar_tanpa_pasal_acuan_belum_benar():
    """Negatif: acuan menyebut Pasal 156, jawaban hanya menyebut UU-nya."""
    # Act
    hasil = _nilai("Pesangon diatur UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("0.00")
    assert hasil.regulation_matches[0].matched is True
    assert _pasal(hasil) == [("uu", "13", 2003, "156", False)]


def test_pasal_lain_dari_peraturan_yang_sama_tidak_memenuhi_acuan():
    """Negatif: Pasal 157 bukan Pasal 156."""
    # Act
    hasil = _nilai("Pesangon diatur Pasal 157 UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("0.00")


def test_pasal_tambahan_tidak_dihukum():
    """Positif: AC2, menyebut Pasal 156 dan Pasal 157 tetap benar untuk acuan Pasal 156."""
    # Act
    hasil = _nilai("Lihat Pasal 157 dan Pasal 156 UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("1.00")


def test_daftar_nomor_setelah_satu_kata_pasal_ikut_dikaitkan():
    """Positif: 'Pasal 155 dan 156 UU 13/2003' menyebut Pasal 156 untuk UU 13/2003."""
    # Act
    hasil = _nilai("Lihat Pasal 155 dan 156 UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("1.00")


def test_daftar_pasal_dengan_koma_sebelum_dan_terbaca_semua():
    """Positif: 'Pasal 156, 157, dan 158 UU 13/2003'; PUEBI memberi koma sebelum 'dan'."""
    # Act
    hasil = _nilai(
        "Lihat Pasal 156, 157, dan 158 UU 13/2003.",
        [rujukan(link("uu", "13", 2003, "158"))],
    )

    # Assert
    assert hasil.score == Decimal("1.00")


def test_daftar_pasal_dengan_koma_sebelum_dan_tidak_jatuh_ke_peraturan_sebelumnya():
    """Negatif: Pasal 157 di daftar itu milik UU 13/2003, bukan UU 6/2023 yang disebut lebih dulu."""
    # Arrange
    jawaban = "UU 6/2023 mengubah banyak hal. Pasal 156, 157, dan 158 UU 13/2003 tetap."

    # Act
    hasil = _nilai(jawaban, [rujukan(link("uu", "6", 2023, "157"))])

    # Assert
    assert hasil.score == Decimal("0.00")


def test_pasal_dikaitkan_ke_peraturan_yang_disebut_sebelumnya():
    """Positif: peraturan disebut dulu, pasalnya di kalimat berikut."""
    # Arrange
    jawaban = "Pesangon diatur UU 13/2003. Pasal 156 mengatur besarannya."

    # Act
    hasil = _nilai(jawaban, [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("1.00")


def test_pasal_milik_peraturan_lain_tidak_dihitung():
    """Negatif: 'Pasal 156 UU 6/2023' adalah pasal UU 6/2023, bukan UU 13/2003."""
    # Arrange
    jawaban = "UU 13/2003 tetap berlaku. Pasal 156 UU 6/2023 mengubah ketentuan lain."

    # Act
    hasil = _nilai(jawaban, [rujukan_ktk_001()])

    # Assert
    assert hasil.score == Decimal("0.00")
    assert hasil.regulation_matches[0].matched is True


def test_pasal_sebelum_kalimat_lain_tidak_dikaitkan_ke_peraturan_berikutnya():
    """Edge: 'Pasal 156 mengatur ..., sedangkan UU 13/2003' bukan satu frasa rujukan."""
    # Arrange
    jawaban = "Pasal 156 mengatur pesangon, sedangkan UU 13/2003 mengatur ketenagakerjaan."

    # Act
    hasil = _nilai(jawaban, [rujukan_ktk_001()])

    # Assert
    assert _pasal(hasil) == [("uu", "13", 2003, "156", False)]


@pytest.mark.parametrize(("spasi", "dikaitkan"), [(80, True), (81, False)])
def test_sela_frasa_paling_panjang_80_karakter(spasi, dikaitkan):
    """Edge: sela lebih dari 80 karakter bukan satu frasa rujukan, walau isinya hanya spasi."""
    # Act
    hasil = _nilai("Pasal 156" + " " * spasi + "UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert _pasal(hasil) == [("uu", "13", 2003, "156", dikaitkan)]


@pytest.mark.parametrize(
    "jawaban",
    [
        # Baris tabel markdown dengan spasi pengisi.
        "Pasal 5" + " " * 24 + "| UU 13/2003",
        # Sela 80 karakter berisi ayat berulang.
        "Pasal 5 " + "ayat 1 " * 11 + "| UU 13/2003",
    ],
)
def test_sela_panjang_yang_bukan_frasa_tetap_cepat(jawaban):
    """Edge: sela yang gagal cocok tidak membuat penilai macet karena backtracking regex."""
    # Arrange
    mulai = time.perf_counter()

    # Act
    _nilai(jawaban, [rujukan_ktk_001()])

    # Assert
    assert time.perf_counter() - mulai < 0.5


def test_kata_di_atas_bukan_nomor_pasal_romawi():
    """Edge: 'pasal di atas' tidak terbaca sebagai Pasal DI; pasal romawi ditulis kapital."""
    # Act
    hasil = _nilai("Lihat UU 13/2003, pasal di atas.", [rujukan_ktk_001()])

    # Assert
    assert _pasal(hasil) == [("uu", "13", 2003, "156", False)]


def test_rangkaian_jo_benar_bila_semua_disebut():
    """Positif: AC2 dan AC10 PBI-21, 'Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023'."""
    # Arrange
    jawaban = "Pesangon diatur Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023."

    # Act
    hasil = _nilai(jawaban, [rujukan_ac10()])

    # Assert
    assert hasil.score == Decimal("1.00")
    assert hasil.all_chained_cited is True
    assert (hasil.reference_correct, hasil.reference_total) == (1, 1)
    assert _pasal(hasil) == [
        ("uu", "13", 2003, "156", True),
        ("uu", "6", 2023, "II", True),
    ]


def test_rangkaian_yang_kurang_satu_peraturan_salah_seluruhnya():
    """Negatif: AC2, jalur deterministik mewajibkan semua peraturan rangkaian disebut."""
    # Act
    hasil = _nilai("Pesangon diatur Pasal 156 UU 13/2003.", [rujukan_ac10()])

    # Assert
    assert hasil.score == Decimal("0.00")
    assert hasil.all_chained_cited is False
    assert [m.matched for m in hasil.regulation_matches] == [True, False]


def test_kasus_tanpa_rangkaian_tidak_punya_penanda_rangkaian():
    """Edge: penanda rangkaian kosong bila acuan tidak berangkai, bukan True semu."""
    # Act
    hasil = _nilai("Pesangon diatur Pasal 156 UU 13/2003.", [rujukan_ktk_001()])

    # Assert
    assert hasil.all_chained_cited is None


def test_satu_rangkaian_dan_satu_rujukan_tunggal_dihitung_per_rujukan():
    """Positif: skor per rujukan acuan; rangkaian salah, rujukan tunggal benar = 0.50."""
    # Arrange
    acuan = [rujukan_ac10(), rujukan(link("pp", "35", 2021, "40"))]
    jawaban = "Pesangon diatur Pasal 156 UU 13/2003. Besarannya: Pasal 40 PP 35/2021."

    # Act
    hasil = _nilai(jawaban, acuan)

    # Assert
    assert hasil.score == Decimal("0.50")
    assert hasil.all_chained_cited is False


def test_rangkaian_dengan_link_lama_tanpa_tahun_tidak_bisa_dinilai():
    """Edge: satu link tanpa tahun membuat seluruh rangkaian tidak bisa dinilai."""
    # Arrange
    lama = {**link("uu", "6", 2023, "II", connector="jo"), "year": None}
    acuan = [rujukan(link("uu", "13", 2003, "156"), lama)]

    # Act
    hasil = _nilai("Pasal 156 UU 13/2003 jo. Pasal II UU 6.", acuan)

    # Assert
    assert hasil.score is None
    assert hasil.ungradable_refs == ("Pasal 156 UU 13/2003 jo. Pasal II UU 6",)
