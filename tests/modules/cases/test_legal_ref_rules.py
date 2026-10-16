"""Unit test aturan simpan rujukan hukum di modul validasi pusat (PBI-21, SCRUM-160).

Murni, tanpa HTTP dan database: validate_payload menerima dict body kasus.
Keputusan PO 17 Okt: field yang tidak dipakai bentuknya ditolak (bukan
diabaikan), dengan kode FIELD_NOT_ALLOWED di field itu.

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). Nilai ayat "1", huruf "a", angka "1", dan teks bebas adalah input
format sintetis.
"""

from typing import Any

import pytest

from app.modules.cases.validation import (
    CONNECTOR_INVALID,
    FIELD_NOT_ALLOWED,
    FIELD_REQUIRED,
    validate_payload,
)
from app.shared.exceptions import ValidationError
from tests.legal_refs import link_pasal, rujukan_ac10, rujukan_ktk_001

TEKS_BEBAS = {"kind": "free_text", "links": [], "free_text": "Format teks bebas"}


def _body(*rujukan: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_code": "PHK-001",
        "identity": {"title": "PHK sepihak", "question": "Apakah sah?"},
        "split_tag": "dev",
        "legal_refs": list(rujukan),
    }


def _ditolak(*rujukan: dict[str, Any]) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        validate_payload(_body(*rujukan))
    return info.value


def _nama_saja(**isi: Any) -> dict[str, Any]:
    rujukan = rujukan_ktk_001()
    link = rujukan["links"][0]
    link["form"] = "name_only"
    del link["pasal"]
    link.update(isi)
    return rujukan


# --- positif -----------------------------------------------------------------


def test_contoh_ac10_dan_ktk_001_lolos():
    validate_payload(_body(rujukan_ktk_001(), rujukan_ac10(), TEKS_BEBAS))


def test_nama_peraturan_saja_tanpa_unit_lolos():
    validate_payload(_body(_nama_saja()))


@pytest.mark.parametrize("bentuk", ["pasal", "pasal_angka"])
def test_ayat_dan_huruf_boleh_untuk_bentuk_berpasal(bentuk):
    rujukan = rujukan_ac10()
    link = rujukan["links"][1]
    link.update(form=bentuk, ayat="1", huruf="a")
    if bentuk == "pasal":
        link["angka"] = None

    validate_payload(_body(rujukan))


@pytest.mark.parametrize("kosong", [None, "", "   "])
def test_field_kosong_tidak_dianggap_terisi(kosong):
    """Isian kosong atau spasi dari editor bukan pelanggaran bentuk."""
    rujukan = _nama_saja(pasal=kosong, angka=kosong, ayat=kosong, huruf=kosong)
    terstruktur = {**rujukan_ktk_001(), "free_text": kosong}

    validate_payload(_body(rujukan, terstruktur))


# --- negatif: field yang tidak dipakai bentuk link ---------------------------


@pytest.mark.parametrize("nama", ["pasal", "angka", "ayat", "huruf"])
def test_nama_peraturan_saja_menolak_unit(nama):
    salah = _ditolak(_nama_saja(**{nama: "1" if nama != "huruf" else "a"}))

    assert salah.code == FIELD_NOT_ALLOWED
    assert salah.field == f"legal_refs[0].links[0].{nama}"
    assert salah.message == (
        f"Field legal_refs[0].links[0].{nama} tidak boleh diisi untuk bentuk Nama Peraturan"
    )


def test_bentuk_pasal_menolak_angka():
    rujukan = rujukan_ac10()
    rujukan["links"][0]["angka"] = "1"

    salah = _ditolak(rujukan)

    assert salah.code == FIELD_NOT_ALLOWED
    assert salah.field == "legal_refs[0].links[0].angka"
    assert salah.message.endswith("untuk bentuk Pasal")


# --- negatif: kind rujukan ---------------------------------------------------


def test_teks_bebas_menolak_link():
    rujukan = {**TEKS_BEBAS, "links": rujukan_ktk_001()["links"]}

    salah = _ditolak(rujukan)

    assert salah.code == FIELD_NOT_ALLOWED
    assert salah.field == "legal_refs[0].links"
    assert salah.message == "Field legal_refs[0].links harus kosong untuk Teks Bebas"


def test_rujukan_terstruktur_menolak_teks_bebas():
    salah = _ditolak({**rujukan_ktk_001(), "free_text": "Format teks bebas"})

    assert salah.code == FIELD_NOT_ALLOWED
    assert salah.field == "legal_refs[0].free_text"
    assert salah.message == "Field legal_refs[0].free_text hanya untuk Teks Bebas"


# --- urutan error ------------------------------------------------------------


def test_menunjuk_rujukan_dan_link_yang_salah_bukan_yang_pertama():
    rujukan = rujukan_ac10()
    rujukan["links"][1]["ayat"] = "1"
    rujukan["links"][1]["form"] = "pasal"
    rujukan["links"][1]["angka"] = None
    rusak = _nama_saja(huruf="a")

    salah = _ditolak(rujukan, rusak)

    assert salah.field == "legal_refs[1].links[0].huruf"


def test_isian_wajib_dilaporkan_sebelum_field_terlarang_di_rujukan_yang_sama():
    rujukan = rujukan_ac10()
    rujukan["links"][0]["angka"] = "1"
    del rujukan["links"][1]["number"]

    salah = _ditolak(rujukan)

    assert salah.code == FIELD_REQUIRED
    assert salah.field == "legal_refs[0].links[1].number"


def test_rujukan_sebelumnya_yang_salah_dilaporkan_lebih_dulu():
    salah_bentuk = _nama_saja(pasal="1")
    kurang = rujukan_ktk_001()
    del kurang["links"][0]["year"]

    salah = _ditolak(salah_bentuk, kurang)

    assert salah.code == FIELD_NOT_ALLOWED
    assert salah.field == "legal_refs[0].links[0].pasal"


# --- rantai penghubung (AC6) ----------------------------------------------------


def _rantai(*penghubung: str) -> dict[str, Any]:
    """Rujukan dengan satu link per penghubung. Nomor dan tahun input format sintetis."""
    links = [
        {**link_pasal("1", number=str(nomor), year=2020), "connector": hubung}
        for nomor, hubung in enumerate(penghubung, start=1)
    ]
    return {"kind": "structured", "links": links}


@pytest.mark.parametrize(
    "penghubung",
    [("none",), ("none", "jo"), ("none", "jis", "jis"), ("none", "jo", "jis")],
    ids=["satu-link", "jo", "jis-tiga-peraturan", "jo-lalu-jis"],
)
def test_rantai_sah_lolos(penghubung):
    validate_payload(_body(_rantai(*penghubung)))


@pytest.mark.parametrize("penghubung", ["jo", "jis"])
def test_link_pertama_tidak_boleh_memakai_penghubung(penghubung):
    salah = _ditolak(_rantai(penghubung, "jo"))

    assert salah.code == CONNECTOR_INVALID
    assert salah.field == "legal_refs[0].links[0].connector"
    assert salah.message == "Peraturan pertama tidak memakai penghubung"


@pytest.mark.parametrize(
    ("penghubung", "posisi"),
    [(("none", "none"), 1), (("none", "jis", "none"), 2), (("none", "none", "jo"), 1)],
    ids=["link-kedua", "link-terakhir", "link-tengah"],
)
def test_link_berikutnya_wajib_jo_atau_jis(penghubung, posisi):
    salah = _ditolak(_rantai(*penghubung))

    assert salah.code == CONNECTOR_INVALID
    assert salah.field == f"legal_refs[0].links[{posisi}].connector"
    assert salah.message == (f"Peraturan ke-{posisi + 1} wajib disambung dengan jo. atau jis.")


def test_field_terlarang_di_link_sebelumnya_dilaporkan_lebih_dulu():
    rujukan = _rantai("none", "none")
    rujukan["links"][0]["angka"] = "1"

    salah = _ditolak(rujukan)

    assert salah.code == FIELD_NOT_ALLOWED
    assert salah.field == "legal_refs[0].links[0].angka"


def test_penghubung_link_pertama_dilaporkan_sebelum_field_terlarang_link_kedua():
    """Urutan baca: semua aturan link 0 lebih dulu, baru link 1."""
    rujukan = _rantai("jo", "jo")
    rujukan["links"][1]["angka"] = "1"

    salah = _ditolak(rujukan)

    assert salah.code == CONNECTOR_INVALID
    assert salah.field == "legal_refs[0].links[0].connector"
