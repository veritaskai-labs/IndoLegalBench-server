"""Teks kosong yang tidak terlihat. SCRUM-214, catatan Adra di #89.

trim() PostgreSQL hanya membuang spasi biasa, dan str.strip() Python tidak membuang
zero-width space. Akibatnya label sesi atau alasan "terima apa adanya" yang isinya
hanya tab, baris baru, non-breaking space, atau zero-width space tetap tersimpan.
app.shared.text memegang satu daftar karakter kosong yang dipakai skema, service,
dan CHECK database, supaya ketiganya sepakat.

Partisi masukan: spasi ASCII, spasi Unicode (str.isspace), karakter format tak terlihat
(kategori Cf), dan teks nyata. Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: masukan yang harus ditolak atau tidak boleh diubah.
- Edge: batas atau kasus pinggir.
"""

import importlib
import sys
import unicodedata

import pytest

TAK_TERLIHAT = {
    "\u180e": "mongolian-vowel-separator",
    "\u200b": "zero-width-space",
    "\u200c": "zero-width-non-joiner",
    "\u200d": "zero-width-joiner",
    "\u2060": "word-joiner",
    "\ufeff": "bom",
}


@pytest.fixture
def teks():
    # Lewat fixture: modul yang belum ada gagal per tes, bukan menghentikan pengumpulan.
    return importlib.import_module("app.shared.text")


def test_semua_spasi_unicode_termasuk_karakter_kosong(teks):
    """Positif: setiap karakter yang str.isspace() ada di daftar, juga bila Python menambahnya."""
    # Act
    spasi = {chr(kode) for kode in range(sys.maxunicode + 1) if chr(kode).isspace()}

    # Assert
    assert spasi <= set(teks.BLANK_CHARS)


@pytest.mark.parametrize("karakter", list(TAK_TERLIHAT), ids=list(TAK_TERLIHAT.values()))
def test_karakter_format_tak_terlihat_termasuk_karakter_kosong(teks, karakter):
    """Positif: zero-width dan BOM tidak terlihat di layar, jadi tidak dihitung sebagai isi."""
    assert karakter in teks.BLANK_CHARS


def test_daftar_hanya_berisi_spasi_atau_format_tak_terlihat(teks):
    """Negatif: huruf, angka, tanda baca, dan emoji tidak boleh ikut terbuang."""
    # Act
    lain = [c for c in teks.BLANK_CHARS if not c.isspace() and unicodedata.category(c) != "Cf"]

    # Assert
    assert lain == []
    assert len(teks.BLANK_CHARS) == len(set(teks.BLANK_CHARS))


def test_literal_sql_aman_dan_sama_dengan_daftar(teks):
    """Edge: tanpa kutip atau backslash, literal SQL tidak bisa pecah dan isinya sama persis."""
    # Assert
    assert "'" not in teks.BLANK_CHARS
    assert "\\" not in teks.BLANK_CHARS
    assert f"'{teks.BLANK_CHARS}'" == teks.BLANK_CHARS_SQL


@pytest.mark.parametrize(
    "masukan",
    ["", " ", "\t\n\r", "\u00a0", "\u200b\u200b", "\ufeff", "\u3000", " \t\u00a0\u200b\u2060 "],
    ids=["kosong", "spasi", "tab-baris", "nbsp", "zwsp", "bom", "spasi-ideografik", "campuran"],
)
def test_teks_yang_hanya_berisi_karakter_kosong_menjadi_kosong(teks, masukan):
    """Positif: setelah dibersihkan tidak tersisa apa pun, jadi pemanggil bisa menolaknya."""
    assert teks.strip_blank(masukan) == ""


def test_karakter_kosong_di_tepi_dibuang(teks):
    """Positif: tepi dibersihkan dari semua jenis karakter kosong, bukan hanya spasi."""
    assert teks.strip_blank("\u200b\t Uji coba Gemini\u00a0\ufeff\n") == "Uji coba Gemini"


@pytest.mark.parametrize(
    "masukan",
    ["Uji\u200bcoba", "a\u00a0b", "Run\tNovember", "👩\u200d💻"],
    ids=["zwsp-tengah", "nbsp-tengah", "tab-tengah", "emoji-zwj"],
)
def test_karakter_kosong_di_tengah_tidak_diubah(teks, masukan):
    """Negatif: isi di tengah tidak disentuh, misalnya emoji yang disambung zero-width joiner."""
    assert teks.strip_blank(masukan) == masukan
