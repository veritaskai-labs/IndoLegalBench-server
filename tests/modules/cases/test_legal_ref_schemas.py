"""Unit test schema link rujukan hukum (PBI-21, SCRUM-160).

Hanya aturan yang bisa ditulis di schema (dan ikut ke zod lewat OpenAPI):
tipe, enum, panjang, pola pasal/angka, dan pembersihan spasi. Aturan lintas
field (bentuk, rantai jo./jis., tahun <= tahun berjalan, jenis ada di daftar)
diuji di langkah validasi.

Contoh hukum hanya dua: KTK-001 (Pasal 156 UU 13/2003) dan contoh AC10
"Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023" (dari Aileen, belum
dikonfirmasi tim legal). Nilai lain ("1", "1A", "IV", 2020, dst.) adalah
input format sintetis, bukan contoh tim legal.
"""

import pytest
from pydantic import ValidationError as KesalahanPydantic

from app.modules.cases.schemas import LegalRefLinkRead, LegalRefLinkWrite
from app.modules.cases.validation import FIELD_REQUIRED, response_from_pydantic

# KTK-001 (Sprint 1): Pasal 156 UU 13/2003.
LINK_KTK_001 = {
    "connector": "none",
    "regulation_type": "uu",
    "number": "13",
    "year": 2003,
    "pasal": "156",
}

# Link kedua contoh AC10: "jo. Pasal II Angka 5 UU 6/2023".
LINK_AC10_KEDUA = {
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

    assert link.connector == "jo"
    assert link.pasal == "II"
    assert link.angka == "5"


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
    link = LegalRefLinkWrite.model_validate(
        _link(regulation_type="x" * 150, number="1" * 40, year=9999)
    )

    assert len(link.regulation_type) == 150
    assert len(link.number) == 40


def test_display_text_dari_client_diabaikan():
    """display_text dibentuk server, jadi tidak ada di schema tulis."""
    link = LegalRefLinkWrite.model_validate(_link(display_text="teks dari client"))

    assert "display_text" not in link.model_dump()


# --- negatif -----------------------------------------------------------------


@pytest.mark.parametrize("nama", ["connector", "regulation_type", "number", "year"])
def test_field_wajib_hilang_ditolak(nama):
    assert _gagal_di(_link(**{nama: None})) == [nama]


@pytest.mark.parametrize("nama", ["regulation_type", "number"])
def test_field_wajib_kosong_atau_spasi_ditolak(nama):
    for kosong in ("", "   "):
        assert _gagal_di(_link(**{nama: kosong})) == [nama]


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
        ("number", "1" * 41),
        ("about", "x" * 501),
        ("ayat", "1" * 21),
        ("huruf", "a" * 9),
        ("angka", "1" * 21),
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
