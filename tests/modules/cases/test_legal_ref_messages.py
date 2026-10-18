"""Teks pesan FIELD_REQUIRED dan FIELD_NOT_ALLOWED rujukan hukum (PBI-21, SCRUM-162).

Keputusan PO 18 Okt: pesan rujukan ditulis sebagai kalimat biasa dengan label
yang sama seperti form editor, supaya simpan dan pratinjau memakai satu bahasa
error (Design hal. 24). Kode dan path field TIDAK berubah, karena client
memetakan error lewat path. Tiap tes mengecek ketiganya sekaligus.

Contoh hukum hanya KTK-001 dan contoh AC10 (dari Aileen, belum dikonfirmasi
tim legal). Nilai lain ("1", "a", teks bebas) adalah input format sintetis.
"""

from typing import Any

import pytest

from app.modules.cases.validation import (
    FIELD_NOT_ALLOWED,
    FIELD_REQUIRED,
    response_from_pydantic,
    validate_payload,
)
from app.shared.exceptions import ValidationError
from app.shared.security import Role
from tests.legal_refs import rujukan_ac10, rujukan_ktk_001
from tests.modules.cases.test_cases import _buat, _suite

TEKS_BEBAS = {"kind": "free_text", "links": [], "free_text": "Format teks bebas"}


def _body(*rujukan: Any) -> dict[str, Any]:
    return {
        "case_code": "PHK-001",
        "identity": {"title": "PHK sepihak", "question": "Apakah sah?"},
        "split_tag": "dev",
        "legal_refs": list(rujukan),
    }


def _ditolak(*rujukan: Any) -> tuple[str, str, str]:
    """(code, field, message) dari aturan simpan."""
    with pytest.raises(ValidationError) as info:
        validate_payload(_body(*rujukan), tahun_ini=2026)
    return info.value.code, info.value.field or "", info.value.message


def _dari_pydantic(loc: tuple[Any, ...], **galat: Any) -> dict[str, str]:
    """Satu galat Pydantic dipetakan seperti handler 422 di main.py."""
    return response_from_pydantic([{"loc": ("body", *loc), **galat}])


def _nama_saja(**isi: Any) -> dict[str, Any]:
    rujukan = rujukan_ktk_001()
    link = rujukan["links"][0]
    link["form"] = "name_only"
    del link["pasal"]
    link.update(isi)
    return rujukan


# --- FIELD_REQUIRED dari aturan simpan --------------------------------------


def test_kind_kosong():
    assert _ditolak({"links": []}) == (
        FIELD_REQUIRED,
        "legal_refs[0].kind",
        "Bentuk rujukan wajib diisi.",
    )


def test_teks_bebas_kosong():
    assert _ditolak({**TEKS_BEBAS, "free_text": "   "}) == (
        FIELD_REQUIRED,
        "legal_refs[0].free_text",
        "Teks bebas wajib diisi.",
    )


def test_rujukan_terstruktur_tanpa_peraturan():
    assert _ditolak({"kind": "structured", "links": []}) == (
        FIELD_REQUIRED,
        "legal_refs[0].links",
        "Rujukan terstruktur butuh minimal satu peraturan.",
    )


def test_rujukan_bukan_objek_menyebut_nomor_rujukan():
    """Rujukan kedua yang rusak: nomor urut dihitung dari 1, bukan indeks 0."""
    assert _ditolak(rujukan_ktk_001(), "x") == (
        FIELD_REQUIRED,
        "legal_refs[1]",
        "Rujukan ke-2 wajib diisi.",
    )


def test_peraturan_bukan_objek_menyebut_nomor_peraturan():
    rujukan = rujukan_ac10()
    rujukan["links"][1] = None

    assert _ditolak(rujukan) == (
        FIELD_REQUIRED,
        "legal_refs[0].links[1]",
        "Peraturan ke-2 wajib diisi.",
    )


def test_bentuk_peraturan_kosong():
    rujukan = rujukan_ktk_001()
    del rujukan["links"][0]["form"]

    assert _ditolak(rujukan) == (
        FIELD_REQUIRED,
        "legal_refs[0].links[0].form",
        "Bentuk peraturan wajib diisi.",
    )


@pytest.mark.parametrize(
    ("nama", "label"),
    [("regulation_type", "Jenis peraturan"), ("number", "Nomor peraturan"), ("year", "Tahun")],
)
def test_field_wajib_peraturan_memakai_label_form(nama, label):
    rujukan = rujukan_ktk_001()
    rujukan["links"][0][nama] = ""

    assert _ditolak(rujukan) == (
        FIELD_REQUIRED,
        f"legal_refs[0].links[0].{nama}",
        f"{label} wajib diisi.",
    )


def test_pasal_kosong_di_bentuk_pasal():
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["pasal"] = ""

    assert _ditolak(rujukan) == (
        FIELD_REQUIRED,
        "legal_refs[0].links[0].pasal",
        "Pasal wajib diisi untuk bentuk Pasal.",
    )


def test_pasal_kosong_di_bentuk_pasal_angka():
    """Bentuk diambil dari link yang salah (link ke-2 AC10), bukan link pertama."""
    rujukan = rujukan_ac10()
    rujukan["links"][1]["pasal"] = ""

    assert _ditolak(rujukan) == (
        FIELD_REQUIRED,
        "legal_refs[0].links[1].pasal",
        "Pasal wajib diisi untuk bentuk Pasal + Angka.",
    )


def test_angka_kosong_di_bentuk_pasal_angka():
    rujukan = rujukan_ac10()
    del rujukan["links"][1]["angka"]

    assert _ditolak(rujukan) == (
        FIELD_REQUIRED,
        "legal_refs[0].links[1].angka",
        "Angka wajib diisi untuk bentuk Pasal + Angka.",
    )


# --- FIELD_NOT_ALLOWED -------------------------------------------------------


@pytest.mark.parametrize(
    ("nama", "nilai", "label"),
    [
        ("pasal", "1", "Pasal"),
        ("angka", "1", "Angka"),
        ("ayat", "1", "Ayat"),
        ("huruf", "a", "Huruf"),
    ],
)
def test_unit_di_bentuk_nama_peraturan(nama, nilai, label):
    assert _ditolak(_nama_saja(**{nama: nilai})) == (
        FIELD_NOT_ALLOWED,
        f"legal_refs[0].links[0].{nama}",
        f"{label} tidak boleh diisi untuk bentuk Nama Peraturan.",
    )


def test_angka_di_bentuk_pasal():
    rujukan = rujukan_ac10()
    rujukan["links"][0]["angka"] = "1"

    assert _ditolak(rujukan) == (
        FIELD_NOT_ALLOWED,
        "legal_refs[0].links[0].angka",
        "Angka tidak boleh diisi untuk bentuk Pasal.",
    )


def test_teks_bebas_berisi_peraturan():
    assert _ditolak({**TEKS_BEBAS, "links": rujukan_ktk_001()["links"]}) == (
        FIELD_NOT_ALLOWED,
        "legal_refs[0].links",
        "Teks Bebas tidak boleh berisi peraturan.",
    )


def test_rujukan_terstruktur_berisi_teks_bebas():
    assert _ditolak({**rujukan_ktk_001(), "free_text": "Format teks bebas"}) == (
        FIELD_NOT_ALLOWED,
        "legal_refs[0].free_text",
        "Teks bebas hanya untuk bentuk Teks Bebas.",
    )


# --- FIELD_REQUIRED dari schema (handler 422) --------------------------------


def test_pydantic_field_hilang_di_link():
    assert _dari_pydantic(
        ("legal_refs", 0, "links", 0, "number"), type="missing", msg="Field required"
    ) == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[0].links[0].number",
        "message": "Nomor peraturan wajib diisi.",
    }


def test_pydantic_teks_kosong_di_link():
    assert _dari_pydantic(
        ("legal_refs", 2, "links", 1, "regulation_type"),
        type="string_too_short",
        msg="String should have at least 1 character",
        input="",
    ) == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[2].links[1].regulation_type",
        "message": "Jenis peraturan wajib diisi.",
    }


def test_pydantic_tahun_null_rujukan_lama():
    """Rujukan Sprint 1 tanpa tahun (keputusan PO 17 Okt): tetap FIELD_REQUIRED di year."""
    assert _dari_pydantic(
        ("legal_refs", 0, "links", 0, "year"),
        type="int_type",
        msg="Input should be a valid integer",
        input=None,
    ) == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[0].links[0].year",
        "message": "Tahun wajib diisi.",
    }


def test_pydantic_rujukan_null():
    assert _dari_pydantic(
        ("legal_refs", 0), type="model_type", msg="Input should be an object", input=None
    ) == {"code": FIELD_REQUIRED, "field": "legal_refs[0]", "message": "Rujukan ke-1 wajib diisi."}


def test_pydantic_kind_hilang():
    assert _dari_pydantic(("legal_refs", 0, "kind"), type="missing", msg="Field required") == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[0].kind",
        "message": "Bentuk rujukan wajib diisi.",
    }


def test_pydantic_penghubung_hilang():
    assert _dari_pydantic(
        ("legal_refs", 0, "links", 1, "connector"), type="missing", msg="Field required"
    ) == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[0].links[1].connector",
        "message": "Penghubung wajib diisi.",
    }


# --- di luar cakupan: teks lama tetap ----------------------------------------


@pytest.mark.parametrize(
    "loc",
    [
        ("identity", "title"),
        ("legal_refs",),
        # Field yang tidak dikenal form editor tetap memakai teks umum.
        ("legal_refs", 0, "links", 0, "catatan"),
    ],
)
def test_field_lain_tetap_teks_umum(loc):
    hasil = _dari_pydantic(loc, type="missing", msg="Field required")

    field = hasil["field"]
    assert hasil == {
        "code": FIELD_REQUIRED,
        "field": field,
        "message": f"Field {field} wajib diisi",
    }


def test_validation_error_pydantic_tidak_disentuh():
    """VALIDATION_ERROR (pola pasal) tetap memakai pesan Pydantic, sesuai keputusan PO."""
    hasil = _dari_pydantic(
        ("legal_refs", 0, "links", 0, "pasal"),
        type="string_pattern_mismatch",
        msg="String should match pattern",
        input="IIII",
    )

    assert hasil == {
        "code": "VALIDATION_ERROR",
        "field": "legal_refs[0].links[0].pasal",
        "message": "String should match pattern",
    }


# --- lewat HTTP: body 422 yang dibaca client ---------------------------------


def test_http_simpan_nomor_kosong(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["number"] = " "

    # Act
    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    # Assert
    assert respons.status_code == 422
    assert respons.json() == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[0].links[0].number",
        "message": "Nomor peraturan wajib diisi.",
    }


def test_http_simpan_pasal_kosong(as_role):
    # Arrange
    client = as_role(Role.AUTHOR)
    rujukan = rujukan_ktk_001()
    rujukan["links"][0]["pasal"] = ""

    # Act
    respons = _buat(client, _suite(client), legal_refs=[rujukan])

    # Assert
    assert respons.status_code == 422
    assert respons.json() == {
        "code": FIELD_REQUIRED,
        "field": "legal_refs[0].links[0].pasal",
        "message": "Pasal wajib diisi untuk bentuk Pasal.",
    }
