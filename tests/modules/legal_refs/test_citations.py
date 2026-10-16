"""Test pembaca rujukan dari case_versions.content (PBI-21 AC9, SCRUM-160).

Setelah 160, content["legal_refs"] bisa berisi bentuk baru (kind/links) atau
bentuk Sprint 1 (regulation_type, regulation_number, ...). Pembaca harus
mendeteksi bentuknya: hanya entri lama yang dipetakan lewat map_legacy_refs
(SCRUM-159), karena pemeta itu melewati entri bentuk baru tanpa jejak.

Pencari jenis di-mock seperti di test pemeta 159, jadi test ini tidak membaca
seed atau DB. Contoh hukum: KTK-001 dan contoh AC10 (belum dikonfirmasi tim
legal). Nilai lain input format sintetis.
"""

from unittest.mock import Mock

import pytest

from app.modules.legal_refs import type_lookup
from app.modules.legal_refs.citations import to_citations
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001

KENAL = {"uu": "uu"}


@pytest.fixture
def lookup():
    pencari = Mock(spec=type_lookup.RegulationTypeLookup)
    pencari.resolve.side_effect = lambda teks: KENAL.get(teks.casefold())
    return pencari


def _lama(**kolom):
    """KTK-001 dalam bentuk Sprint 1."""
    dasar = {"regulation_type": "uu", "regulation_number": "13", "year": 2003, "pasal": "156"}
    return {**dasar, **kolom}


def _link(**kolom):
    """Link hasil pemetaan: semua field ada, yang tidak ada di Sprint 1 bernilai None."""
    dasar = {
        "form": "pasal",
        "connector": "none",
        "regulation_type": "uu",
        "number": "13",
        "year": 2003,
        "about": None,
        "pasal": "156",
        "angka": None,
        "ayat": None,
        "huruf": None,
    }
    return {**dasar, **kolom}


@pytest.mark.parametrize("isi", [None, "uu 13", {"kind": "structured"}])
def test_bukan_daftar_dianggap_tanpa_rujukan(isi, lookup):
    assert to_citations(isi, lookup) == []


def test_bentuk_baru_diteruskan_apa_adanya_tanpa_pemeta(lookup):
    daftar = [rujukan_ktk_001(), rujukan_ac10()]

    hasil = to_citations(daftar, lookup)

    assert hasil == [rujukan_ktk_001(), rujukan_ac10()]
    lookup.resolve.assert_not_called()


def test_ktk_001_bentuk_sprint_1_menjadi_bentuk_baru(lookup):
    assert to_citations([_lama()], lookup) == [
        {"kind": "structured", "free_text": None, "links": [_link()]}
    ]


def test_ayat_dan_huruf_lama_ikut_terbawa(lookup):
    hasil = to_citations([_lama(ayat="1", huruf="a")], lookup)

    assert hasil[0]["links"] == [_link(ayat="1", huruf="a")]


def test_rujukan_lama_tanpa_tahun_tetap_terbaca(lookup):
    hasil = to_citations([_lama(year=None)], lookup)

    assert hasil[0]["links"][0]["year"] is None


def test_rujukan_lama_tanpa_pasal_menjadi_nama_peraturan(lookup):
    hasil = to_citations([_lama(pasal=None)], lookup)

    assert hasil[0]["links"] == [_link(form="name_only", pasal=None)]


def test_jenis_lama_yang_tidak_dikenal_menjadi_teks_bebas_tanpa_kehilangan_isi(lookup):
    hasil = to_citations([_lama(regulation_type="Format jenis lain")], lookup)

    assert hasil == [
        {
            "kind": "free_text",
            "free_text": "Format jenis lain Nomor 13 Tahun 2003, Pasal 156",
            "links": [],
        }
    ]


def test_teks_lama_menjadi_teks_bebas_dan_entri_kosong_dilewati(lookup):
    hasil = to_citations(["Format teks lama", "   ", {}], lookup)

    assert hasil == [{"kind": "free_text", "free_text": "Format teks lama", "links": []}]


def test_campuran_bentuk_baru_dan_lama_menjaga_urutan(lookup):
    hasil = to_citations([rujukan_ac10(), _lama(), rujukan_ktk_001()], lookup)

    assert hasil == [
        rujukan_ac10(),
        {"kind": "structured", "free_text": None, "links": [_link()]},
        rujukan_ktk_001(),
    ]
