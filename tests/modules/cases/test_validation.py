"""Unit test modul validasi kasus.

PBI-3, SCRUM-106. Tiap aturan diuji di sini, terpisah dari HTTP.
TODO(Klarifikasi #7): case_code pattern.
TODO(SCRUM-107): completeness formula.
"""

import pytest
from pydantic import ValidationError as KesalahanPydantic

from app.modules.cases.schemas import CaseWrite
from app.modules.cases.validation import (
    CASE_CODE_INVALID,
    FIELD_REQUIRED,
    PLACEHOLDER_CASE_CODE_PATTERN,
    SPLIT_TAG_REQUIRED,
    VALIDATION_ERROR,
    completeness,
    field_path,
    response_from_pydantic,
    validate_payload,
)
from app.shared.exceptions import ValidationError

RUJUKAN = {
    "regulation_type": "uu",
    "regulation_number": "13",
    "year": 2003,
    "pasal": "151",
}


def _data(**ubah):
    data = {
        "case_code": "PHK-001",
        "identity": {"title": "PHK sepihak", "question": "Apakah sah?", "category": None},
        "legal_refs": [dict(RUJUKAN)],
        "answer_criteria": {
            "must_contain": ["surat"],
            "must_not_contain": [],
            "expected_conclusion": "tidak sah",
        },
        "traps": [{"description": "Mencampur upah", "expected_model_behavior": "menolak"}],
        "split_tag": "dev",
    }
    data.update(ubah)
    return data


def test_isian_lengkap_lolos():
    validate_payload(_data())


def test_pola_placeholder_tertulis_di_konstanta():
    assert PLACEHOLDER_CASE_CODE_PATTERN.startswith("^")
    assert "A-Za-z0-9" in PLACEHOLDER_CASE_CODE_PATTERN


@pytest.mark.parametrize("kode", ["", " ", "a", "ada spasi", "kode!"])
def test_case_code_placeholder_ditolak(kode):
    with pytest.raises(ValidationError) as info:
        validate_payload(_data(case_code=kode))

    assert info.value.field == "case_code"
    if kode.strip():
        assert info.value.code == CASE_CODE_INVALID
    else:
        assert info.value.code == FIELD_REQUIRED


@pytest.mark.parametrize(
    ("identitas", "field"),
    [
        (None, "identity"),
        ({}, "identity.title"),
        ({"title": "  ", "question": "Ada"}, "identity.title"),
        ({"title": "Ada", "question": ""}, "identity.question"),
    ],
)
def test_identitas_wajib(identitas, field):
    with pytest.raises(ValidationError) as info:
        validate_payload(_data(identity=identitas))

    assert info.value.code == FIELD_REQUIRED
    assert info.value.field == field


@pytest.mark.parametrize("tag", [None, "", "train"])
def test_split_tag_wajib(tag):
    with pytest.raises(ValidationError) as info:
        validate_payload(_data(split_tag=tag))

    assert info.value.code == SPLIT_TAG_REQUIRED
    assert info.value.field == "split_tag"


def test_tanpa_rujukan_ditolak():
    with pytest.raises(ValidationError) as info:
        validate_payload(_data(legal_refs=[]))

    assert info.value.code == FIELD_REQUIRED
    assert info.value.field == "legal_refs"


@pytest.mark.parametrize("hilang", ["regulation_type", "regulation_number", "pasal"])
def test_rujukan_wajib_sampai_pasal(hilang):
    rujukan = dict(RUJUKAN)
    rujukan[hilang] = "  "
    with pytest.raises(ValidationError) as info:
        validate_payload(_data(legal_refs=[rujukan]))

    assert info.value.code == FIELD_REQUIRED
    assert info.value.field == f"legal_refs[0].{hilang}"


def test_kelengkapan_penuh_seratus_persen():
    hasil = completeness(_data())

    assert hasil["pct"] == 100
    assert hasil["missing"] == []
    assert hasil["contract"] == "placeholder"


def test_kelengkapan_tanpa_jebakan_dan_kriteria_belum_penuh():
    hasil = completeness(
        _data(answer_criteria={"must_contain": [], "must_not_contain": []}, traps=[])
    )

    assert hasil["pct"] == 71
    assert hasil["missing"] == ["answer_criteria", "traps"]
    assert hasil["contract"] == "placeholder"


def test_field_path_rujukan():
    assert field_path(("body", "legal_refs", 0, "pasal")) == "legal_refs[0].pasal"


def test_error_pydantic_split_tag_didahulukan():
    body = response_from_pydantic(
        [
            {"type": "missing", "loc": ("body", "identity", "title")},
            {"type": "missing", "loc": ("body", "split_tag")},
        ]
    )

    assert body["code"] == SPLIT_TAG_REQUIRED
    assert body["field"] == "split_tag"


def test_error_pydantic_pola_case_code():
    body = response_from_pydantic(
        [{"type": "string_pattern_mismatch", "loc": ("body", "case_code")}]
    )

    assert body["code"] == CASE_CODE_INVALID
    assert body["field"] == "case_code"


def _error_codes_skema(data: dict) -> dict[str, str]:
    with pytest.raises(KesalahanPydantic) as info:
        CaseWrite.model_validate(data)
    return response_from_pydantic(info.value.errors())


def test_judul_terlalu_panjang_bukan_wajib():
    identitas = dict(_data()["identity"])
    identitas["title"] = "x" * 301

    body = _error_codes_skema(_data(identity=identitas))

    assert body["code"] == VALIDATION_ERROR
    assert body["field"] == "identity.title"
    assert "300" in body["message"]
    assert "wajib diisi" not in body["message"]
    assert "required" not in body["message"].lower()


def test_tahun_nol_bukan_wajib():
    rujukan = dict(RUJUKAN)
    rujukan["year"] = 0

    body = _error_codes_skema(_data(legal_refs=[rujukan]))

    assert body["code"] == VALIDATION_ERROR
    assert body["field"] == "legal_refs[0].year"
    assert "wajib diisi" not in body["message"]


def test_tipe_json_salah_bukan_wajib():
    identitas = dict(_data()["identity"])
    identitas["title"] = 123

    body = _error_codes_skema(_data(identity=identitas))

    assert body["code"] == VALIDATION_ERROR
    assert body["field"] == "identity.title"
    assert "wajib diisi" not in body["message"]


def test_pasal_hilang_tetap_wajib():
    rujukan = dict(RUJUKAN)
    del rujukan["pasal"]

    body = _error_codes_skema(_data(legal_refs=[rujukan]))

    assert body["code"] == FIELD_REQUIRED
    assert body["field"] == "legal_refs[0].pasal"
    assert body["message"] == "Field legal_refs[0].pasal wajib diisi"


def test_string_kosong_tetap_wajib():
    identitas = dict(_data()["identity"])
    identitas["title"] = ""

    body = _error_codes_skema(_data(identity=identitas))

    assert body["code"] == FIELD_REQUIRED
    assert body["field"] == "identity.title"


def test_split_tag_hilang_tetap_khusus():
    data = _data()
    del data["split_tag"]

    body = _error_codes_skema(data)

    assert body["code"] == SPLIT_TAG_REQUIRED
    assert body["field"] == "split_tag"


def test_pola_case_code_skema_tetap_khusus():
    body = _error_codes_skema(_data(case_code="kode tidak sah"))

    assert body["code"] == CASE_CODE_INVALID
    assert body["field"] == "case_code"
