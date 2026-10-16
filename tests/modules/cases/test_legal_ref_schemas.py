"""Unit test schema rujukan hukum dan link-nya (PBI-21, SCRUM-160).

Model datar (keputusan PO 17 Okt): rujukan punya kind structured/free_text,
setiap link punya form name_only/pasal/pasal_angka.

Hanya aturan yang bisa ditulis di schema (dan ikut ke zod lewat OpenAPI):
tipe, enum, panjang, pola pasal/angka, dan pembersihan spasi. Aturan lintas
field (bentuk, rantai jo./jis., tahun <= tahun berjalan, jenis ada di daftar)
diuji di langkah validasi.

Contoh hukum hanya dua: KTK-001 (Pasal 156 UU 13/2003) dan contoh AC10
"Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023" (dari Aileen, belum
dikonfirmasi tim legal). Nilai lain ("1", "1A", "IV", 2020, dst.) adalah
input format sintetis, bukan contoh tim legal.
"""

from typing import get_args

import pytest
from pydantic import ValidationError as KesalahanPydantic

from app.modules.cases.schemas import (
    Connector,
    LegalRefKind,
    LegalRefLinkRead,
    LegalRefLinkWrite,
    LegalRefStored,
    LegalRefWrite,
    LinkForm,
)
from app.modules.cases.validation import FIELD_REQUIRED, response_from_pydantic
from app.modules.legal_refs import models as tabel

# KTK-001 (Sprint 1): Pasal 156 UU 13/2003.
LINK_KTK_001 = {
    "form": "pasal",
    "connector": "none",
    "regulation_type": "uu",
    "number": "13",
    "year": 2003,
    "pasal": "156",
}

# Link kedua contoh AC10: "jo. Pasal II Angka 5 UU 6/2023".
LINK_AC10_KEDUA = {
    "form": "pasal_angka",
    "connector": "jo",
    "regulation_type": "uu",
    "number": "6",
    "year": 2023,
    "pasal": "II",
    "angka": "5",
}

# Kode terpanjang di seed regulation_types (113 karakter).
KODE_TERPANJANG = (
    "peraturan_menteri_kependudukan_dan_pembangunan_keluarga_"
    "kepala_badan_kependudukan_dan_keluarga_berencana_nasional"
)


def _link(**ubah):
    """Link KTK-001 dengan field yang diubah. Nilai None menghapus field."""
    data = dict(LINK_KTK_001)
    for nama, nilai in ubah.items():
        if nilai is None:
            data.pop(nama, None)
        else:
            data[nama] = nilai
    return data


def _gagal_di(data: dict) -> list[str]:
    """Nama field yang ditolak schema tulis."""
    with pytest.raises(KesalahanPydantic) as info:
        LegalRefLinkWrite.model_validate(data)
    return [".".join(str(bagian) for bagian in error["loc"]) for error in info.value.errors()]


# --- positif -----------------------------------------------------------------


def test_link_ktk_001_lolos():
    link = LegalRefLinkWrite.model_validate(LINK_KTK_001)

    assert link.form == "pasal"
    assert link.connector == "none"
    assert link.regulation_type == "uu"
    assert link.number == "13"
    assert link.year == 2003
    assert link.pasal == "156"
    assert link.angka is None
    assert link.about is None
    assert link.ayat is None
    assert link.huruf is None


def test_link_kedua_ac10_pasal_romawi_dan_angka_lolos():
    link = LegalRefLinkWrite.model_validate(LINK_AC10_KEDUA)

    assert link.form == "pasal_angka"
    assert link.connector == "jo"
    assert link.pasal == "II"
    assert link.angka == "5"


@pytest.mark.parametrize("bentuk", ["name_only", "pasal", "pasal_angka"])
def test_tiga_bentuk_link_diterima(bentuk):
    assert LegalRefLinkWrite.model_validate(_link(form=bentuk)).form == bentuk


@pytest.mark.parametrize("penghubung", ["none", "jo", "jis"])
def test_tiga_penghubung_diterima(penghubung):
    assert LegalRefLinkWrite.model_validate(_link(connector=penghubung)).connector == penghubung


@pytest.mark.parametrize(
    "pasal",
    ["1", "10", "1A", "I", "IV", "IX", "XL", "XC", "CD", "CM", "MCMXC"],
    ids=lambda nilai: f"format-{nilai}",
)
def test_pasal_angka_biasa_berhuruf_atau_romawi_diterima(pasal):
    """AC5 plus pasal sisipan berakhiran huruf (usulan PO, menunggu Steven)."""
    assert LegalRefLinkWrite.model_validate(_link(pasal=pasal)).pasal == pasal


def test_nomor_boleh_alfanumerik_dengan_titik_dan_garis_miring():
    """Format input dari chat PO, bukan contoh tim legal."""
    link = LegalRefLinkWrite.model_validate(_link(number="PER.01/MEN/2006", year=2006))

    assert link.number == "PER.01/MEN/2006"


def test_field_opsional_terisi_disimpan():
    link = LegalRefLinkWrite.model_validate(
        _link(about="Format tentang", ayat="1", huruf="a", angka="1")
    )

    assert (link.about, link.ayat, link.huruf, link.angka) == ("Format tentang", "1", "a", "1")


def test_spasi_di_sekitar_isian_dibuang():
    link = LegalRefLinkWrite.model_validate(
        _link(
            regulation_type=" uu ",
            number=" 13 ",
            pasal=" 156 ",
            angka=" 1 ",
            about=" Format tentang ",
            ayat=" 1 ",
            huruf=" a ",
        )
    )

    assert link.regulation_type == "uu"
    assert link.number == "13"
    assert link.pasal == "156"
    assert link.angka == "1"
    assert link.about == "Format tentang"
    assert link.ayat == "1"
    assert link.huruf == "a"


@pytest.mark.parametrize("nama", ["about", "pasal", "angka", "ayat", "huruf"])
def test_opsional_kosong_atau_spasi_jadi_none(nama):
    for kosong in ("", "   "):
        assert getattr(LegalRefLinkWrite.model_validate(_link(**{nama: kosong})), nama) is None


def test_kode_jenis_terpanjang_di_seed_diterima():
    link = LegalRefLinkWrite.model_validate(_link(regulation_type=KODE_TERPANJANG))

    assert len(link.regulation_type) == 113


def test_batas_panjang_tepat_diterima():
    """Sama dengan kolom legal_ref_links di SCRUM-159: number 100, angka 40."""
    link = LegalRefLinkWrite.model_validate(
        _link(regulation_type="x" * 150, number="1" * 100, angka="1" * 40, year=9999)
    )

    assert len(link.regulation_type) == 150
    assert len(link.number) == 100
    assert len(link.angka or "") == 40


def test_display_text_dari_client_diabaikan():
    """display_text dibentuk server, jadi tidak ada di schema tulis."""
    link = LegalRefLinkWrite.model_validate(_link(display_text="teks dari client"))

    assert "display_text" not in link.model_dump()


# --- negatif -----------------------------------------------------------------


@pytest.mark.parametrize("nama", ["form", "connector", "regulation_type", "number", "year"])
def test_field_wajib_hilang_ditolak(nama):
    assert _gagal_di(_link(**{nama: None})) == [nama]


@pytest.mark.parametrize("nama", ["regulation_type", "number"])
def test_field_wajib_kosong_atau_spasi_ditolak(nama):
    for kosong in ("", "   "):
        assert _gagal_di(_link(**{nama: kosong})) == [nama]


@pytest.mark.parametrize("nama", ["regulation_type", "number", "pasal", "angka"])
def test_tipe_json_bukan_string_ditolak(nama):
    """Angka JSON (13, bukan "13") tidak diubah diam-diam jadi teks."""
    assert _gagal_di(_link(**{nama: 13})) == [nama]


def test_opsional_null_eksplisit_tetap_none():
    data = _link()
    data["about"] = None
    data["pasal"] = None

    link = LegalRefLinkWrite.model_validate(data)

    assert (link.about, link.pasal) == (None, None)


@pytest.mark.parametrize("bentuk", ["free_text", "structured", "Pasal", "pasal-angka", ""])
def test_bentuk_link_di_luar_enum_ditolak(bentuk):
    """free_text adalah kind rujukan, bukan bentuk link."""
    assert _gagal_di(_link(form=bentuk)) == ["form"]


@pytest.mark.parametrize("penghubung", ["jo.", "jis.", "JO", "dan", ""])
def test_penghubung_di_luar_enum_ditolak(penghubung):
    assert _gagal_di(_link(connector=penghubung)) == ["connector"]


@pytest.mark.parametrize(
    "pasal",
    ["IIII", "VV", "IC", "iv", "1a", "1AB", "0", "01", "1.5", "Pasal 1", "-1", "I V"],
    ids=lambda nilai: f"format-{nilai}",
)
def test_pasal_format_salah_ditolak(pasal):
    assert _gagal_di(_link(pasal=pasal)) == ["pasal"]


@pytest.mark.parametrize("angka", ["0", "01", "1A", "I", "-1", "1.5"])
def test_angka_bukan_digit_ditolak(angka):
    assert _gagal_di(_link(angka=angka)) == ["angka"]


@pytest.mark.parametrize("tahun", [0, -1, 10000])
def test_tahun_di_luar_rentang_skema_ditolak(tahun):
    assert _gagal_di(_link(year=tahun)) == ["year"]


@pytest.mark.parametrize(
    ("nama", "nilai"),
    [
        ("regulation_type", "x" * 151),
        ("number", "1" * 101),
        ("about", "x" * 501),
        ("ayat", "1" * 21),
        ("huruf", "a" * 9),
        ("angka", "1" * 41),
        ("pasal", "1" * 41),
    ],
)
def test_melebihi_panjang_maksimum_ditolak(nama, nilai):
    assert _gagal_di(_link(**{nama: nilai})) == [nama]


def test_tahun_null_dipetakan_ke_field_required():
    """Keputusan PO 17 Okt: tahun kosong di rujukan lama ditolak dengan FIELD_REQUIRED."""
    data = _link()
    data["year"] = None
    with pytest.raises(KesalahanPydantic) as info:
        LegalRefLinkWrite.model_validate(data)

    body = response_from_pydantic(info.value.errors())

    assert body["code"] == FIELD_REQUIRED
    assert body["field"] == "year"


# --- schema baca (data lama, AC9) ----------------------------------------------


def test_baca_menerima_tahun_null_dari_migrasi():
    data = _link()
    data["year"] = None

    link = LegalRefLinkRead.model_validate(data)

    assert link.year is None


def test_baca_tidak_memakai_pola_pasal():
    """Pasal lama berupa string bebas tetap tampil; perbaikannya saat Author menyimpan."""
    link = LegalRefLinkRead.model_validate(_link(pasal="1a"))

    assert link.pasal == "1a"


# --- rujukan (kind + links) ------------------------------------------------------

# Contoh AC10: "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023".
RUJUKAN_AC10 = {"kind": "structured", "links": [LINK_KTK_001, LINK_AC10_KEDUA]}


def _rujukan_gagal_di(data: dict) -> list[str]:
    """Lokasi field yang ditolak schema tulis rujukan."""
    with pytest.raises(KesalahanPydantic) as info:
        LegalRefWrite.model_validate(data)
    return [".".join(str(bagian) for bagian in error["loc"]) for error in info.value.errors()]


def test_rujukan_ac10_dua_link_dengan_jo_lolos():
    rujukan = LegalRefWrite.model_validate(RUJUKAN_AC10)

    assert rujukan.kind == "structured"
    assert rujukan.free_text is None
    assert [link.form for link in rujukan.links] == ["pasal", "pasal_angka"]
    assert [link.connector for link in rujukan.links] == ["none", "jo"]
    assert [(link.pasal, link.angka) for link in rujukan.links] == [("156", None), ("II", "5")]


def test_rujukan_ktk_001_satu_link_lolos():
    rujukan = LegalRefWrite.model_validate({"kind": "structured", "links": [LINK_KTK_001]})

    assert len(rujukan.links) == 1
    assert rujukan.links[0].number == "13"


def test_rujukan_teks_bebas_disimpan_apa_adanya_tanpa_spasi_tepi():
    """AC7. Isi teks bebas adalah input format sintetis."""
    rujukan = LegalRefWrite.model_validate(
        {"kind": "free_text", "links": [], "free_text": "  Format teks bebas  "}
    )

    assert rujukan.kind == "free_text"
    assert rujukan.links == []
    assert rujukan.free_text == "Format teks bebas"


@pytest.mark.parametrize("kosong", ["", "   "])
def test_teks_bebas_kosong_jadi_none(kosong):
    rujukan = LegalRefWrite.model_validate({"kind": "free_text", "links": [], "free_text": kosong})

    assert rujukan.free_text is None


def test_teks_bebas_tepat_2000_karakter_diterima():
    rujukan = LegalRefWrite.model_validate(
        {"kind": "free_text", "links": [], "free_text": "x" * 2000}
    )

    assert len(rujukan.free_text or "") == 2000


def test_display_text_rujukan_dari_client_diabaikan():
    rujukan = LegalRefWrite.model_validate({**RUJUKAN_AC10, "display_text": "teks dari client"})

    assert "display_text" not in rujukan.model_dump()


@pytest.mark.parametrize("jenis", ["name_only", "pasal", "pasal_angka", "Structured", ""])
def test_kind_di_luar_enum_ditolak(jenis):
    """Bentuk link bukan kind rujukan."""
    assert _rujukan_gagal_di({**RUJUKAN_AC10, "kind": jenis}) == ["kind"]


@pytest.mark.parametrize("nama", ["kind", "links"])
def test_kind_dan_links_wajib_dikirim(nama):
    data = dict(RUJUKAN_AC10)
    del data[nama]

    assert _rujukan_gagal_di(data) == [nama]


def test_links_bukan_list_ditolak():
    assert _rujukan_gagal_di({**RUJUKAN_AC10, "links": LINK_KTK_001}) == ["links"]


def test_teks_bebas_melebihi_2000_ditolak():
    data = {"kind": "free_text", "links": [], "free_text": "x" * 2001}

    assert _rujukan_gagal_di(data) == ["free_text"]


def test_error_link_kedua_menunjuk_indeks_link():
    """Lokasi error ikut indeks link, dasar path legal_refs[i].links[j].x (AC8)."""
    rusak = dict(LINK_AC10_KEDUA, pasal="IIII")

    assert _rujukan_gagal_di({**RUJUKAN_AC10, "links": [LINK_KTK_001, rusak]}) == ["links.1.pasal"]


def test_baca_rujukan_lama_hasil_migrasi():
    """AC9: rujukan lama tanpa tahun tetap terbaca."""
    lama = dict(LINK_KTK_001, year=None)

    rujukan = LegalRefStored.model_validate(
        {"kind": "structured", "links": [lama], "free_text": None}
    )

    assert rujukan.links[0].year is None
    assert isinstance(rujukan.links[0], LegalRefLinkRead)


# --- penjaga: kontrak sama dengan tabel SCRUM-159 ------------------------------
# models.py legal_refs hanya boleh diimpor dari modulnya sendiri, jadi schema
# cases menulis nilainya sendiri. Tes ini yang memastikan keduanya tidak menyimpang.


def _nilai_literal(tipe) -> set[str]:
    return set(get_args(tipe))


def test_enum_kontrak_sama_dengan_enum_tabel():
    assert _nilai_literal(LegalRefKind) == {item.value for item in tabel.LegalRefKind}
    assert _nilai_literal(LinkForm) == {item.value for item in tabel.LegalRefLinkForm}
    assert _nilai_literal(Connector) == {item.value for item in tabel.LegalRefConnector}


def _batas_skema(properti: dict) -> int:
    if "maxLength" in properti:
        return properti["maxLength"]
    return next(cabang["maxLength"] for cabang in properti["anyOf"] if "maxLength" in cabang)


@pytest.mark.parametrize("skema", [LegalRefLinkWrite, LegalRefLinkRead])
@pytest.mark.parametrize("kolom", ["regulation_type", "number", "pasal", "angka", "ayat", "huruf"])
def test_batas_panjang_kontrak_sama_dengan_kolom_tabel(skema, kolom):
    properti = skema.model_json_schema()["properties"][kolom]

    assert _batas_skema(properti) == tabel.LegalRefLink.__table__.c[kolom].type.length
