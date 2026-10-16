"""Unit test penulisan rujukan sesuai praktik hukum (PBI-21 AC6, AC10; SCRUM-160).

Builder murni: info jenis peraturan (label, singkatan) diberikan sebagai
parameter, jadi tes tidak membaca tabel regulation_types.

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). "PER.01/MEN/2006" adalah input format dari aturan display_text di
Jira SCRUM-160, bukan contoh tim legal. Nomor 1/2/3 dan tahun 2020-2022 adalah
input format sintetis.
"""

import pytest

from app.modules.cases.display_text import TypeName, citation_text
from app.modules.cases.schemas import LegalRefStored
from tests.legal_refs import link_pasal, rujukan_ac10, rujukan_ktk_001

# Label dan singkatan diambil dari seed regulation_types (SCRUM-159).
JENIS = {
    "uu": TypeName(label="Undang-undang", singkatan="UU"),
    "permen": TypeName(label="Peraturan Kementerian", singkatan="Permen"),
    "peraturan_menteri_ketenagakerjaan": TypeName(
        label="Peraturan Menteri Ketenagakerjaan", singkatan=None
    ),
}
TANPA_SINGKATAN = "peraturan_menteri_ketenagakerjaan"


def _teks(rujukan: dict) -> str:
    return citation_text(LegalRefStored.model_validate(rujukan), JENIS)


def _nama_saja(regulation_type: str, number: str, year: int | None) -> dict:
    link = link_pasal("1", regulation_type=regulation_type, number=number)
    link.update(form="name_only", pasal=None, year=year)
    return {"kind": "structured", "links": [link]}


# --- contoh AC10 dan KTK-001 ---------------------------------------------------


def test_contoh_ac10_ditulis_sesuai_penulisan_hukum():
    assert _teks(rujukan_ac10()) == "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"


def test_ktk_001_tampil_sama_seperti_sprint_1():
    """AC9: KTK-001 tetap terbaca sebagai Pasal 156 UU 13/2003."""
    assert _teks(rujukan_ktk_001()) == "Pasal 156 UU 13/2003"


def test_nama_peraturan_saja_tanpa_pasal():
    assert _teks(_nama_saja("uu", "13", 2003)) == "UU 13/2003"


# --- aturan nama peraturan (Jira SCRUM-160) -------------------------------------


def test_aturan_1_jenis_bersingkatan_memakai_nomor_garis_miring_tahun():
    assert _teks(_nama_saja("uu", "1", 2020)) == "UU 1/2020"


def test_aturan_2_jenis_tanpa_singkatan_memakai_label_nomor_tahun():
    assert (
        _teks(_nama_saja(TANPA_SINGKATAN, "1", 2020))
        == "Peraturan Menteri Ketenagakerjaan Nomor 1 Tahun 2020"
    )


@pytest.mark.parametrize(
    ("jenis", "harapan"),
    [
        ("permen", "Permen PER.01/MEN/2006"),
        (TANPA_SINGKATAN, "Peraturan Menteri Ketenagakerjaan Nomor PER.01/MEN/2006"),
    ],
)
def test_aturan_3_nomor_berakhiran_tahun_tidak_mengulang_tahun(jenis, harapan):
    assert _teks(_nama_saja(jenis, "PER.01/MEN/2006", 2006)) == harapan


@pytest.mark.parametrize(
    ("jenis", "harapan"),
    [
        ("permen", "Permen PER.01/MEN/2006/2007"),
        (TANPA_SINGKATAN, "Peraturan Menteri Ketenagakerjaan Nomor PER.01/MEN/2006 Tahun 2007"),
    ],
)
def test_aturan_4_akhiran_nomor_beda_dengan_tahun_tetap_menulis_tahun(jenis, harapan):
    """Di luar AC, tanpa validasi baru. Menunggu konfirmasi tim legal (Steven)."""
    assert _teks(_nama_saja(jenis, "PER.01/MEN/2006", 2007)) == harapan


def test_angka_tahun_tanpa_garis_miring_bukan_akhiran_tahun():
    """'12020' tidak berakhiran '/2020', jadi tahun tetap ditulis."""
    assert _teks(_nama_saja("uu", "12020", 2020)) == "UU 12020/2020"


# --- rantai dan bentuk --------------------------------------------------------


def test_rantai_jis_lebih_dari_dua_peraturan():
    links = [
        link_pasal("1", number="1", year=2020),
        {**link_pasal("2", number="2", year=2021), "connector": "jis"},
        {**link_pasal("3", number="3", year=2022), "connector": "jis"},
    ]

    assert (
        _teks({"kind": "structured", "links": links})
        == "Pasal 1 UU 1/2020 jis. Pasal 2 UU 2/2021 jis. Pasal 3 UU 3/2022"
    )


def test_bentuk_campuran_dalam_satu_rantai():
    links = [
        _nama_saja("uu", "1", 2020)["links"][0],
        {**link_pasal("II", number="2", year=2021), "connector": "jo"},
    ]

    assert _teks({"kind": "structured", "links": links}) == "UU 1/2020 jo. Pasal II UU 2/2021"


def test_teks_bebas_ditampilkan_apa_adanya():
    """AC7. Isi teks bebas adalah input format sintetis."""
    rujukan = {"kind": "free_text", "links": [], "free_text": "Format teks bebas, apa adanya"}

    assert _teks(rujukan) == "Format teks bebas, apa adanya"


# --- data lama (AC9) dan jenis yang tidak ada di daftar -----------------------


@pytest.mark.parametrize(
    ("jenis", "harapan"),
    [("uu", "UU 13"), (TANPA_SINGKATAN, "Peraturan Menteri Ketenagakerjaan Nomor 13")],
)
def test_rujukan_lama_tanpa_tahun_tidak_menulis_tahun_kosong(jenis, harapan):
    assert _teks(_nama_saja(jenis, "13", None)) == harapan


def test_jenis_di_luar_daftar_memakai_kodenya():
    """Tidak terjadi lewat API (jenis divalidasi), tapi tampilan tidak boleh meledak."""
    assert _teks(_nama_saja("kode_lain", "1", 2020)) == "kode_lain Nomor 1 Tahun 2020"


# --- ayat dan huruf (keputusan PO 17 Okt): Pasal, Angka, ayat, huruf, lalu nama ---


def _pasal(**isi) -> dict:
    """Satu link berbentuk pasal dengan input format sintetis (Pasal 1 UU 1/2020)."""
    link = link_pasal("1", number="1", year=2020)
    link.update(isi)
    return {"kind": "structured", "links": [link]}


def test_ayat_dan_huruf_setelah_pasal():
    assert _teks(_pasal(ayat="1", huruf="a")) == "Pasal 1 ayat (1) huruf a UU 1/2020"


def test_angka_tepat_setelah_pasal_sebelum_ayat():
    rujukan = _pasal(form="pasal_angka", angka="2", ayat="3", huruf="b")

    assert _teks(rujukan) == "Pasal 1 Angka 2 ayat (3) huruf b UU 1/2020"


def test_huruf_tanpa_ayat():
    assert _teks(_pasal(huruf="a")) == "Pasal 1 huruf a UU 1/2020"


def test_ayat_yang_sudah_berkurung_tidak_dikurung_dua_kali():
    """Data Sprint 1 bisa menyimpan ayat sebagai "(1)"."""
    assert _teks(_pasal(ayat="(1)")) == "Pasal 1 ayat (1) UU 1/2020"


def test_tentang_tidak_ditampilkan():
    assert _teks(_pasal(about="Format tentang")) == "Pasal 1 UU 1/2020"
