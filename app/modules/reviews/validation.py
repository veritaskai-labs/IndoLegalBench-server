"""Pesan 422 untuk body penilaian reviewer (POST /reviews/{id}/verdict).

Error bawaan Pydantic seperti "Extra inputs are not permitted" tidak
menjelaskan aturannya. Di sini kegagalan body diterjemahkan ke bentuk error
tim, {code, message, field}, sama seperti body kasus di cases.validation.
"""

from typing import Any

SPLIT_TAG_NOT_REVIEWED = "SPLIT_TAG_NOT_REVIEWED"
UNKNOWN_FIELD = "UNKNOWN_FIELD"
FIELD_REQUIRED = "FIELD_REQUIRED"
VALIDATION_ERROR = "VALIDATION_ERROR"

_INVALID_MESSAGE = "Isian penilaian tidak valid"
_TAG_MESSAGE = (
    "Tag dev/test bukan bagian penilaian. Tag kasus hanya ditampilkan sebagai info; "
    "kirim decision dan comment saja."
)


def verdict_error_from_pydantic(errors: list[dict[str, Any]]) -> dict[str, str]:
    """Satu error untuk ditampilkan. Tag yang ikut dikirim didahulukan (PBI-9 AC2)."""
    for error in errors:
        if error.get("type") == "extra_forbidden" and _field(error) == "split_tag":
            return {"code": SPLIT_TAG_NOT_REVIEWED, "message": _TAG_MESSAGE, "field": "split_tag"}
    if not errors:
        return {"code": VALIDATION_ERROR, "message": _INVALID_MESSAGE}
    return _map(errors[0])


def _map(error: dict[str, Any]) -> dict[str, str]:
    field = _field(error)
    tipe = str(error.get("type", ""))
    if tipe == "extra_forbidden":
        return {
            "code": UNKNOWN_FIELD,
            "message": f"Field {field} tidak dikenal. Kirim decision dan comment saja.",
            "field": field,
        }
    if field == "decision" and tipe == "missing":
        return {
            "code": FIELD_REQUIRED,
            "message": "Field decision wajib diisi: approve atau revise.",
            "field": field,
        }
    if field == "decision":
        return {
            "code": VALIDATION_ERROR,
            "message": "decision harus approve atau revise.",
            "field": field,
        }
    if not field:
        return {"code": VALIDATION_ERROR, "message": _INVALID_MESSAGE}
    return {
        "code": VALIDATION_ERROR,
        "message": str(error.get("msg") or _INVALID_MESSAGE),
        "field": field,
    }


def _field(error: dict[str, Any]) -> str:
    """Nama field teratas di body, mis. ('body', 'split_tag') menjadi split_tag."""
    loc = [str(item) for item in error.get("loc", ()) if item != "body"]
    return loc[0] if loc and not loc[0].isdigit() else ""
