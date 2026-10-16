"""Test pemeta rujukan Sprint 1 ke bentuk tabel baru.

PBI-21, SCRUM-159 AC9. Bentuk lama: regulation_type, regulation_number,
year (opsional), pasal, ayat, huruf. Rujukan lama harus tampil sama dan
tidak boleh ada data yang hilang: yang tidak bisa dipetakan ke jenis
resmi disimpan sebagai teks bebas dengan seluruh nilai aslinya.

Pencari jenis peraturan di-mock, jadi test ini tidak membaca seed atau DB.
"""

from unittest.mock import Mock

import pytest

from app.modules.legal_refs import legacy_mapping, models, type_lookup

KENAL = {"uu": "uu", "pp": "peraturan_pemerintah"}


@pytest.fixture
def lookup():
    pencari = Mock(spec=type_lookup.RegulationTypeLookup)
    pencari.resolve.side_effect = lambda teks: KENAL.get(teks.casefold())
    return pencari


def _lama(**kolom):
    dasar = {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "156"}
    return {**dasar, **kolom}


def _petakan(isi, lookup):
    return legacy_mapping.map_legacy_refs(isi, lookup)


# --- bentuk terstruktur -------------------------------------------------


def test_rujukan_ktk_001_menjadi_satu_rujukan_pasal_dengan_satu_link(lookup):
    # Arrange: Pasal 156 UU 13/2003
    isi = [_lama()]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert hasil.free_text_fallbacks == 0
    assert hasil.skipped == 0
    assert len(hasil.refs) == 1
    rujukan = hasil.refs[0]
    assert (rujukan.order, rujukan.kind, rujukan.free_text) == (
        1,
        models.LegalRefKind.STRUCTURED,
        None,
    )
    assert rujukan.links == [
        legacy_mapping.LegalRefLinkDraft(
            position=1,
            connector=models.LegalRefConnector.NONE,
            form=models.LegalRefLinkForm.PASAL,
            regulation_type="uu",
            number="13",
            year=2003,
            pasal="156",
            ayat=None,
            huruf=None,
        )
    ]


def test_ayat_dan_huruf_lama_ikut_terbawa(lookup):
    # Act
    hasil = _petakan([_lama(ayat="2", huruf="a")], lookup)

    # Assert
    link = hasil.refs[0].links[0]
    assert (link.pasal, link.ayat, link.huruf) == ("156", "2", "a")


def test_jenis_lama_berupa_singkatan_dipetakan_ke_code(lookup):
    # Act
    hasil = _petakan([_lama(regulation_type="PP")], lookup)

    # Assert
    assert hasil.refs[0].links[0].regulation_type == "peraturan_pemerintah"


def test_pencari_dipanggil_dengan_teks_yang_sudah_dirapikan(lookup):
    # Act
    _petakan([_lama(regulation_type="  uu  ")], lookup)

    # Assert
    lookup.resolve.assert_called_once_with("uu")


def test_nomor_lama_berupa_angka_dibaca_sebagai_teks(lookup):
    # Act
    hasil = _petakan([_lama(regulation_number=13)], lookup)

    # Assert
    assert hasil.refs[0].links[0].number == "13"


def test_tahun_kosong_tetap_terstruktur_dengan_tahun_none(lookup):
    # Arrange: tahun dulu opsional
    # Act
    hasil = _petakan([_lama(year=None)], lookup)

    # Assert
    assert hasil.free_text_fallbacks == 0
    assert hasil.refs[0].kind == models.LegalRefKind.STRUCTURED
    assert hasil.refs[0].links[0].form == models.LegalRefLinkForm.PASAL
    assert hasil.refs[0].links[0].year is None


@pytest.mark.parametrize("pasal", [None, "", "   "])
def test_pasal_kosong_menjadi_bentuk_nama_peraturan_saja(lookup, pasal):
    # Act
    hasil = _petakan([_lama(pasal=pasal)], lookup)

    # Assert
    rujukan = hasil.refs[0]
    assert rujukan.kind == models.LegalRefKind.STRUCTURED
    assert rujukan.links[0].form == models.LegalRefLinkForm.NAME_ONLY
    assert rujukan.links[0].pasal is None


def test_beberapa_rujukan_diberi_urutan_satu_sampai_n(lookup):
    # Arrange
    isi = [_lama(pasal="1"), _lama(pasal="2"), _lama(pasal="3")]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert [(r.order, r.links[0].pasal) for r in hasil.refs] == [(1, "1"), (2, "2"), (3, "3")]


# --- bukan list atau kosong ---------------------------------------------


@pytest.mark.parametrize("isi", [None, [], {}, "UU 13/2003", 5])
def test_legal_refs_yang_bukan_list_atau_kosong_menghasilkan_tanpa_rujukan(lookup, isi):
    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert hasil.refs == []
    assert hasil.free_text_fallbacks == 0
    assert hasil.skipped == 0


# --- fallback ke teks bebas ---------------------------------------------


def test_jenis_yang_tidak_dikenal_disimpan_sebagai_teks_bebas_dengan_semua_nilai(lookup):
    # Arrange
    isi = [
        _lama(regulation_type="Peraturan Gubernur Kuno", regulation_number="5", year=2001)
        | {"pasal": "3", "ayat": "2", "huruf": "a"}
    ]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    rujukan = hasil.refs[0]
    assert rujukan.kind == models.LegalRefKind.FREE_TEXT
    assert rujukan.links == []
    assert rujukan.free_text == (
        "Peraturan Gubernur Kuno Nomor 5 Tahun 2001, Pasal 3, Ayat 2, Huruf a"
    )
    assert hasil.free_text_fallbacks == 1


def test_teks_bebas_hasil_fallback_melewatkan_bagian_yang_kosong(lookup):
    # Arrange
    isi = [{"regulation_type": "Kuno", "regulation_number": "5", "pasal": "3"}]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert hasil.refs[0].free_text == "Kuno Nomor 5, Pasal 3"


@pytest.mark.parametrize("nomor", [None, "", "  "])
def test_nomor_kosong_menjadi_teks_bebas(lookup, nomor):
    # Act
    hasil = _petakan([_lama(regulation_number=nomor)], lookup)

    # Assert
    assert hasil.refs[0].kind == models.LegalRefKind.FREE_TEXT
    assert hasil.refs[0].free_text == "uu Tahun 2003, Pasal 156"
    assert hasil.free_text_fallbacks == 1


@pytest.mark.parametrize("jenis", [None, "", "  "])
def test_jenis_kosong_menjadi_teks_bebas(lookup, jenis):
    # Act
    hasil = _petakan([_lama(regulation_type=jenis)], lookup)

    # Assert
    assert hasil.refs[0].kind == models.LegalRefKind.FREE_TEXT
    assert hasil.refs[0].free_text == "Nomor 13 Tahun 2003, Pasal 156"


@pytest.mark.parametrize("tahun", [0, -5, 10000, "2003", 2003.5, True])
def test_tahun_yang_tidak_valid_menjadi_teks_bebas_agar_tidak_hilang(lookup, tahun):
    # Act
    hasil = _petakan([_lama(year=tahun)], lookup)

    # Assert
    assert hasil.refs[0].kind == models.LegalRefKind.FREE_TEXT
    assert f"Tahun {tahun}" in hasil.refs[0].free_text
    assert hasil.free_text_fallbacks == 1


@pytest.mark.parametrize(
    ("kolom", "panjang"),
    [("regulation_number", 101), ("pasal", 41), ("ayat", 21), ("huruf", 9)],
)
def test_nilai_lebih_panjang_dari_kolom_baru_menjadi_teks_bebas(lookup, kolom, panjang):
    # Arrange
    isi = [_lama(**{kolom: "9" * panjang})]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert hasil.refs[0].kind == models.LegalRefKind.FREE_TEXT
    assert "9" * panjang in hasil.refs[0].free_text


def test_nilai_tepat_di_batas_panjang_kolom_masih_terstruktur(lookup):
    # Arrange
    isi = [_lama(regulation_number="9" * 100, pasal="9" * 40, ayat="9" * 20, huruf="a" * 8)]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert hasil.refs[0].kind == models.LegalRefKind.STRUCTURED
    assert hasil.refs[0].links[0].form == models.LegalRefLinkForm.PASAL
    assert hasil.free_text_fallbacks == 0


# --- entri yang bukan dict ----------------------------------------------


def test_entri_berupa_teks_disimpan_apa_adanya_sebagai_teks_bebas(lookup):
    # Act
    hasil = _petakan(["  Pasal 156 UU 13/2003  "], lookup)

    # Assert
    assert hasil.refs[0].kind == models.LegalRefKind.FREE_TEXT
    assert hasil.refs[0].free_text == "  Pasal 156 UU 13/2003  "
    assert hasil.free_text_fallbacks == 1


@pytest.mark.parametrize("entri", [None, "", "   ", 42, [], {}, {"pasal": "  "}])
def test_entri_tanpa_isi_dilewati_dan_dihitung(lookup, entri):
    # Act
    hasil = _petakan([entri], lookup)

    # Assert
    assert hasil.refs == []
    assert hasil.skipped == 1


def test_urutan_tetap_rapat_setelah_entri_kosong_dilewati(lookup):
    # Arrange
    isi = [None, _lama(pasal="1"), "", "teks lain", {}]

    # Act
    hasil = _petakan(isi, lookup)

    # Assert
    assert [(r.order, r.kind) for r in hasil.refs] == [
        (1, models.LegalRefKind.STRUCTURED),
        (2, models.LegalRefKind.FREE_TEXT),
    ]
    assert hasil.skipped == 3
    assert hasil.free_text_fallbacks == 1
