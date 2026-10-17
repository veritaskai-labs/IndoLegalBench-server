"""Pesan 422 untuk body penilaian. PBI-9 AC2 (SCRUM-164), temuan UAT.

Error bawaan Pydantic ("Extra inputs are not permitted") tidak menjelaskan
aturannya. Body penilaian yang gagal dijawab dengan bentuk error tim:
{code, message, field}.
"""

import pytest

from app.modules.reviews.validation import verdict_error_from_pydantic


def _galat(tipe: str, *loc: str, **lain) -> dict:
    return {"type": tipe, "loc": ("body", *loc), "msg": "pesan pydantic", **lain}


def test_tag_ditolak_dengan_alasan_yang_jelas():
    # Act
    hasil = verdict_error_from_pydantic([_galat("extra_forbidden", "split_tag", input="dev")])

    # Assert
    assert hasil["code"] == "SPLIT_TAG_NOT_REVIEWED"
    assert hasil["field"] == "split_tag"
    assert "info" in hasil["message"]
    assert "decision" in hasil["message"]


def test_tag_didahulukan_walau_ada_galat_lain():
    # Act
    hasil = verdict_error_from_pydantic(
        [_galat("extra_forbidden", "skor"), _galat("extra_forbidden", "split_tag")]
    )

    # Assert
    assert hasil["code"] == "SPLIT_TAG_NOT_REVIEWED"


def test_field_lain_yang_tidak_dikenal():
    # Act
    hasil = verdict_error_from_pydantic([_galat("extra_forbidden", "skor", input=90)])

    # Assert
    assert hasil == {
        "code": "UNKNOWN_FIELD",
        "message": "Field skor tidak dikenal. Kirim decision dan comment saja.",
        "field": "skor",
    }


def test_decision_kosong():
    # Act
    hasil = verdict_error_from_pydantic([_galat("missing", "decision")])

    # Assert
    assert hasil == {
        "code": "FIELD_REQUIRED",
        "message": "Field decision wajib diisi: approve atau revise.",
        "field": "decision",
    }


def test_decision_tidak_dikenal():
    # Act
    hasil = verdict_error_from_pydantic([_galat("enum", "decision", input="tolak")])

    # Assert
    assert hasil["code"] == "VALIDATION_ERROR"
    assert hasil["message"] == "decision harus approve atau revise."
    assert hasil["field"] == "decision"


@pytest.mark.parametrize("galat", [[], [{"type": "json_invalid", "loc": ("body", 0), "msg": ""}]])
def test_galat_lain_tetap_berbentuk_error_tim(galat):
    # Act
    hasil = verdict_error_from_pydantic(galat)

    # Assert
    assert hasil["code"] == "VALIDATION_ERROR"
    assert hasil["message"]


def test_galat_field_lain_memakai_pesan_pydantic():
    """Misalnya komentar lebih dari 5000 karakter."""
    # Act
    hasil = verdict_error_from_pydantic(
        [{"type": "string_too_long", "loc": ("body", "comment"), "msg": "String terlalu panjang"}]
    )

    # Assert
    assert hasil == {
        "code": "VALIDATION_ERROR",
        "message": "String terlalu panjang",
        "field": "comment",
    }
