"""Modul validasi terpusat untuk kasus hukum.

PBI-3, SCRUM-106. Satu modul dipakai untuk tiga hal:
1. Validator Pydantic saat kasus dibuat atau diubah
2. Kode error HTTP untuk field yang gagal
3. Indikator kelengkapan yang disimpan bersama draft

TODO(Klarifikasi #7): replace the case_code pattern. Keep the rule in this module.
PHK-001 and phk-001 are both allowed today.
TODO(SCRUM-103): adjust the field shape if the signed Case contract differs.
"""

import re
from typing import Any

from app.shared.exceptions import ValidationError
from app.modules.cases import completeness as completeness_module

# TODO(Klarifikasi #7): leading letter or digit, then letters, digits, dot,
# underscore, or hyphen. Not the final pattern. PHK-001 and phk-001 both match.
PLACEHOLDER_CASE_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$"
_POLA_KODE = re.compile(PLACEHOLDER_CASE_CODE_PATTERN)

SPLIT_TAG_REQUIRED = "SPLIT_TAG_REQUIRED"
CASE_CODE_INVALID = "CASE_CODE_INVALID"
FIELD_REQUIRED = "FIELD_REQUIRED"
VALIDATION_ERROR = "VALIDATION_ERROR"

_TAG_SAH = frozenset({"dev", "test"})
_FIELD_RUJUKAN = ("regulation_type", "regulation_number", "pasal")

def validate_payload(data: dict[str, Any]) -> None:
    """Reject a payload that breaks the save rules. A clean return may be stored."""
    _require_case_code(data.get("case_code"))
    _require_identity(data.get("identity"))
    _require_split_tag(data.get("split_tag"))
    _require_legal_refs(data.get("legal_refs"))


def completeness(data: dict[str, Any]) -> dict[str, Any]:
    """Indikator kelengkapan yang disimpan bersama kasus (SCRUM-107)."""
    return completeness_module.evaluate(data)


def response_from_pydantic(errors: list[dict[str, Any]]) -> dict[str, str]:
    """Map FastAPI's validation errors onto this module's error codes.

    A missing field or a blank string becomes FIELD_REQUIRED. Other
    constraints (length, range, JSON type) become VALIDATION_ERROR and keep
    the Pydantic message instead of "wajib diisi".
    """
    if not errors:
        return {"code": VALIDATION_ERROR, "message": "Isian kasus tidak valid"}
    dipetakan = [_map_error(error) for error in errors]
    for kode in (SPLIT_TAG_REQUIRED, CASE_CODE_INVALID):
        for item in dipetakan:
            if item["code"] == kode:
                return item
    return dipetakan[0]


def field_path(loc: Any) -> str:
    """Turn ``('body', 'legal_refs', 0, 'pasal')`` into ``legal_refs[0].pasal``."""
    bagian: list[str] = []
    for item in loc or ():
        if item == "body":
            continue
        if isinstance(item, int):
            if bagian:
                bagian[-1] = f"{bagian[-1]}[{item}]"
            else:
                bagian.append(f"[{item}]")
            continue
        bagian.append(str(item))
    return ".".join(bagian)


def _require_case_code(nilai: Any) -> None:
    """Require a non-blank case_code that matches the temporary pattern."""
    kode = _text(nilai)
    if not kode:
        raise ValidationError("Field case_code wajib diisi", code=FIELD_REQUIRED, field="case_code")
    if _POLA_KODE.fullmatch(kode) is None:
        raise ValidationError(
            "Format case_code belum final. Pola yang dipakai sekarang adalah "
            "placeholder Klarifikasi #7.",
            code=CASE_CODE_INVALID,
            field="case_code",
        )


def _require_identity(nilai: Any) -> None:
    """Require identity.title and identity.question after stripping whitespace."""
    if not isinstance(nilai, dict):
        raise ValidationError("Field identity wajib diisi", code=FIELD_REQUIRED, field="identity")
    for nama in ("title", "question"):
        if not _text(nilai.get(nama)):
            field = f"identity.{nama}"
            raise ValidationError(f"Field {field} wajib diisi", code=FIELD_REQUIRED, field=field)


def _require_split_tag(nilai: Any) -> None:
    """Require split_tag to be dev or test."""
    if nilai not in _TAG_SAH:
        raise ValidationError(
            "Tag dev/test wajib diisi",
            code=SPLIT_TAG_REQUIRED,
            field="split_tag",
        )


def _require_legal_refs(nilai: Any) -> None:
    """Require at least one citation, each with type, number, and pasal."""
    if not isinstance(nilai, list) or len(nilai) < 1:
        raise ValidationError(
            "Minimal satu rujukan hukum sampai level pasal",
            code=FIELD_REQUIRED,
            field="legal_refs",
        )
    for indeks, rujukan in enumerate(nilai):
        if not isinstance(rujukan, dict):
            field = f"legal_refs[{indeks}]"
            raise ValidationError(f"Field {field} wajib diisi", code=FIELD_REQUIRED, field=field)
        for nama in _FIELD_RUJUKAN:
            if not _text(rujukan.get(nama)):
                field = f"legal_refs[{indeks}].{nama}"
                raise ValidationError(
                    f"Field {field} wajib diisi",
                    code=FIELD_REQUIRED,
                    field=field,
                )


def _legal_refs_complete(nilai: Any) -> bool:
    """True when every citation has the required text fields."""
    if not isinstance(nilai, list) or not nilai:
        return False
    return all(
        isinstance(rujukan, dict) and all(_text(rujukan.get(nama)) for nama in _FIELD_RUJUKAN)
        for rujukan in nilai
    )


def _has_answer_criteria(nilai: Any) -> bool:
    """True when answer criteria has a phrase or an expected conclusion."""
    if not isinstance(nilai, dict):
        return False
    for kunci in ("must_contain", "must_not_contain"):
        butir = nilai.get(kunci) or []
        if isinstance(butir, list) and any(_text(item) for item in butir):
            return True
    return bool(_text(nilai.get("expected_conclusion")))


def _has_trap(nilai: Any) -> bool:
    """True when at least one trap has a description."""
    if not isinstance(nilai, list):
        return False
    return any(isinstance(item, dict) and _text(item.get("description")) for item in nilai)


def _text(nilai: Any) -> str:
    """Return the stripped text, or an empty string for None."""
    if nilai is None:
        return ""
    return str(nilai).strip()


def _map_error(error: dict[str, Any]) -> dict[str, str]:
    """Map one Pydantic error onto FIELD_REQUIRED, a specific code, or VALIDATION_ERROR."""
    field = field_path(error.get("loc", ()))
    tipe = str(error.get("type", ""))
    if field == "split_tag" or field.endswith(".split_tag"):
        return {
            "code": SPLIT_TAG_REQUIRED,
            "message": "Tag dev/test wajib diisi",
            "field": "split_tag",
        }
    if field == "case_code" and "pattern" in tipe:
        return {
            "code": CASE_CODE_INVALID,
            "message": (
                "Format case_code belum final. Pola yang dipakai sekarang adalah "
                "placeholder Klarifikasi #7."
            ),
            "field": "case_code",
        }
    if not field:
        return {"code": VALIDATION_ERROR, "message": "Isian kasus tidak valid"}
    if _missing_or_blank(error):
        return {
            "code": FIELD_REQUIRED,
            "message": f"Field {field} wajib diisi",
            "field": field,
        }
    return {
        "code": VALIDATION_ERROR,
        "message": _pydantic_message(error),
        "field": field,
    }


def _missing_or_blank(error: dict[str, Any]) -> bool:
    """True when the field was omitted or the input is blank after strip."""
    if str(error.get("type", "")) == "missing":
        return True
    nilai = error.get("input")
    return isinstance(nilai, str) and not nilai.strip()


def _pydantic_message(error: dict[str, Any]) -> str:
    """Use Pydantic's message, or a generic one when that message is empty."""
    pesan = error.get("msg")
    if isinstance(pesan, str) and pesan.strip():
        return pesan
    return "Isian kasus tidak valid"
