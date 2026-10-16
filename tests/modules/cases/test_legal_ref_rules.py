"""Unit test aturan simpan rujukan hukum di modul validasi pusat (PBI-21, SCRUM-160).

Murni, tanpa HTTP dan database: validate_payload menerima dict body kasus.
Keputusan PO 17 Okt: field yang tidak dipakai bentuknya ditolak (bukan
diabaikan), dengan kode FIELD_NOT_ALLOWED di field itu.

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). Nilai ayat "1", huruf "a", angka "1", dan teks bebas adalah input
format sintetis.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.modules.cases import validation
from app.modules.cases.validation import (
    CONNECTOR_INVALID,
    FIELD_NOT_ALLOWED,
    FIELD_REQUIRED,
    REGULATION_TYPE_INACTIVE,
    REGULATION_TYPE_UNKNOWN,
    YEAR_IN_FUTURE,
    check_regulation_types,
    current_year_wib,
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


# --- tahun <= tahun berjalan WIB (keputusan PO 17 Okt) ---------------------------


def _tahun(*tahun: int) -> dict[str, Any]:
    """Rantai jo dengan satu link per tahun. Nomor dan tahun input format sintetis."""
    rujukan = _rantai("none", *(["jo"] * (len(tahun) - 1)))
    for link, isi in zip(rujukan["links"], tahun, strict=True):
        link["year"] = isi
    return rujukan


@pytest.mark.parametrize("tahun", [1, 2025, 2026], ids=["batas-bawah", "tahun-lalu", "tahun-ini"])
def test_tahun_sampai_tahun_berjalan_diterima(tahun):
    validate_payload(_body(_tahun(tahun)), tahun_ini=2026)


def test_tahun_setelah_tahun_berjalan_ditolak():
    with pytest.raises(ValidationError) as info:
        validate_payload(_body(_tahun(2020, 2027)), tahun_ini=2026)

    assert info.value.code == YEAR_IN_FUTURE
    assert info.value.field == "legal_refs[0].links[1].year"
    assert info.value.message == "Tahun tidak boleh melewati 2026"


def test_contoh_ac10_ditolak_kalau_jam_mundur_sebelum_2023():
    """Batas ikut jam yang diinjeksi, bukan angka yang ditulis di kode."""
    with pytest.raises(ValidationError) as info:
        validate_payload(_body(rujukan_ac10()), tahun_ini=2022)

    assert info.value.field == "legal_refs[0].links[1].year"


def test_penghubung_salah_dilaporkan_sebelum_tahun_di_link_yang_sama():
    rujukan = _tahun(2020, 2027)
    rujukan["links"][1]["connector"] = "none"

    with pytest.raises(ValidationError) as info:
        validate_payload(_body(rujukan), tahun_ini=2026)

    assert info.value.code == CONNECTOR_INVALID


@pytest.mark.parametrize(
    ("utc", "tahun"),
    [
        (datetime(2026, 12, 31, 16, 59, 59, tzinfo=UTC), 2026),
        (datetime(2026, 12, 31, 17, 0, 0, tzinfo=UTC), 2027),
    ],
    ids=["23.59.59-WIB", "00.00-WIB-tahun-baru"],
)
def test_tahun_berjalan_dihitung_di_wib(utc, tahun):
    """31 Des 17.00 UTC sudah 1 Jan di WIB (UTC+7)."""
    assert current_year_wib(utc) == tahun


def test_tanpa_tahun_injeksi_memakai_jam_wib(monkeypatch):
    monkeypatch.setattr(validation, "current_year_wib", lambda now=None: 2026)

    with pytest.raises(ValidationError) as info:
        validate_payload(_body(_tahun(2027)))

    assert info.value.code == YEAR_IN_FUTURE


def test_jam_sungguhan_mengembalikan_tahun_wib():
    """Tanpa argumen: tahun dari jam sistem di Asia/Jakarta."""
    assert current_year_wib() == datetime.now(validation.WIB).year


# --- jenis peraturan ada di daftar (AC2) -----------------------------------------
# Keputusan PO 17 Okt: jenis nonaktif hanya diterima kalau kodenya sudah ada di
# rujukan tersimpan pada versi yang sama.

AKTIF = {"uu": True, "permen": False}


def _jenis_ditolak(daftar: list, tersimpan: set[str]) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        check_regulation_types(daftar, AKTIF, tersimpan)
    return info.value


def test_jenis_aktif_diterima():
    check_regulation_types([rujukan_ktk_001(), rujukan_ac10()], AKTIF, set())


def test_jenis_tidak_ada_di_daftar_ditolak():
    rujukan = rujukan_ac10()
    rujukan["links"][1]["regulation_type"] = "kode_lain"

    salah = _jenis_ditolak([rujukan], set())

    assert salah.code == REGULATION_TYPE_UNKNOWN
    assert salah.field == "legal_refs[0].links[1].regulation_type"
    assert salah.message == "Jenis peraturan tidak ada di daftar"


def test_jenis_nonaktif_baru_ditolak():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["regulation_type"] = "permen"

    salah = _jenis_ditolak([rujukan_ac10(), rujukan], {"uu"})

    assert salah.code == REGULATION_TYPE_INACTIVE
    assert salah.field == "legal_refs[1].links[0].regulation_type"
    assert salah.message == "Jenis peraturan ini sudah tidak aktif. Pilih jenis lain."


def test_jenis_nonaktif_yang_sudah_tersimpan_di_versi_ini_diterima():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["regulation_type"] = "permen"

    check_regulation_types([rujukan], AKTIF, {"permen"})


def test_teks_bebas_tidak_diperiksa_jenisnya():
    check_regulation_types([TEKS_BEBAS], {}, set())


def test_jenis_pertama_yang_salah_dilaporkan_lebih_dulu():
    tidak_dikenal = rujukan_ktk_001()
    tidak_dikenal["links"][0]["regulation_type"] = "kode_lain"
    nonaktif = rujukan_ktk_001()
    nonaktif["links"][0]["regulation_type"] = "permen"

    salah = _jenis_ditolak([nonaktif, tidak_dikenal], set())

    assert salah.code == REGULATION_TYPE_INACTIVE
    assert salah.field == "legal_refs[0].links[0].regulation_type"
