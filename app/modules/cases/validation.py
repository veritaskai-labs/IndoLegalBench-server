"""Modul validasi terpusat untuk kasus hukum.

PBI-3, SCRUM-106. Satu modul dipakai untuk tiga hal:
1. Validator Pydantic saat kasus dibuat atau diubah
2. Kode error HTTP untuk field yang gagal
3. Indikator kelengkapan yang disimpan bersama draft

TODO(Klarifikasi #7): the case_code pattern lives in completeness.py and is
reused here. PHK-001 and phk-001 are both allowed today.
TODO(SCRUM-103): adjust the field shape if the signed Case contract differs.
"""

import re
from datetime import datetime
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

from app.modules.cases import completeness as completeness_module
from app.shared.exceptions import ValidationError

PLACEHOLDER_CASE_CODE_PATTERN = completeness_module.PLACEHOLDER_CASE_CODE_PATTERN
PASAL_PATTERN = completeness_module.POLA_PASAL
ANGKA_PATTERN = completeness_module.POLA_ANGKA
_POLA_KODE = re.compile(PLACEHOLDER_CASE_CODE_PATTERN)

SPLIT_TAG_REQUIRED = "SPLIT_TAG_REQUIRED"
CASE_CODE_INVALID = "CASE_CODE_INVALID"
FIELD_REQUIRED = "FIELD_REQUIRED"
FIELD_NOT_ALLOWED = "FIELD_NOT_ALLOWED"
CONNECTOR_INVALID = "CONNECTOR_INVALID"
YEAR_IN_FUTURE = "YEAR_IN_FUTURE"

# Tahun berjalan dihitung di zona waktu tim (keputusan PO 17 Okt).
WIB = ZoneInfo("Asia/Jakarta")
VALIDATION_ERROR = "VALIDATION_ERROR"

_TAG_SAH = completeness_module.TAG_SAH
_FIELD_TERLARANG = completeness_module.FIELD_TERLARANG_PER_BENTUK
# Nama bentuk seperti di editor (PBI-21 AC4), untuk pesan error.
_LABEL_BENTUK = {"name_only": "Nama Peraturan", "pasal": "Pasal", "pasal_angka": "Pasal + Angka"}


def validate_payload(data: dict[str, Any], *, tahun_ini: int | None = None) -> None:
    """Reject a payload that breaks the save rules. A clean return may be stored.

    tahun_ini is the current year for the citation year rule. Tests pass it;
    otherwise it comes from current_year_wib().
    """
    _require_case_code(data.get("case_code"))
    _require_identity(data.get("identity"))
    _require_split_tag(data.get("split_tag"))
    tahun = tahun_ini if tahun_ini is not None else current_year_wib()
    _require_legal_refs(data.get("legal_refs"), tahun)


def current_year_wib(now: datetime | None = None) -> int:
    """The current year in Asia/Jakarta. `now` must be timezone-aware when given."""
    saat = now if now is not None else datetime.now(WIB)
    return saat.astimezone(WIB).year


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


def _require_legal_refs(nilai: Any, tahun_ini: int) -> None:
    """Require at least one citation, each complete for its kind and link forms (PBI-21)."""
    if not isinstance(nilai, list) or len(nilai) < 1:
        raise ValidationError(
            completeness_module.PESAN_TANPA_RUJUKAN,
            code=FIELD_REQUIRED,
            field="legal_refs",
        )
    for indeks, rujukan in enumerate(nilai):
        kurang = completeness_module.missing_in_ref(rujukan)
        if kurang is not None:
            field = f"legal_refs[{indeks}]{kurang}"
            raise ValidationError(f"Field {field} wajib diisi", code=FIELD_REQUIRED, field=field)
        _check_ref_rules(f"legal_refs[{indeks}]", rujukan, tahun_ini)


def _check_ref_rules(awalan: str, rujukan: dict[str, Any], tahun_ini: int) -> None:
    """Rules that need a complete citation, link by link in reading order.

    Runs after missing_in_ref, so kind, links, and every form are known to be valid.
    """
    if rujukan["kind"] == "free_text":
        if rujukan.get("links"):
            _not_allowed(f"{awalan}.links", "harus kosong untuk Teks Bebas")
        return
    if _text(rujukan.get("free_text")):
        _not_allowed(f"{awalan}.free_text", "hanya untuk Teks Bebas")
    for posisi, link in enumerate(rujukan["links"]):
        _check_link(f"{awalan}.links[{posisi}]", posisi, link, tahun_ini)


def _check_link(awalan: str, posisi: int, link: dict[str, Any], tahun_ini: int) -> None:
    """Unused fields for the form, the connector (AC6), then the year."""
    bentuk = link["form"]
    for nama in _FIELD_TERLARANG[bentuk]:
        if _text(link.get(nama)):
            _not_allowed(
                f"{awalan}.{nama}", f"tidak boleh diisi untuk bentuk {_LABEL_BENTUK[bentuk]}"
            )
    pesan = _connector_problem(posisi, link.get("connector"))
    if pesan is not None:
        raise ValidationError(pesan, code=CONNECTOR_INVALID, field=f"{awalan}.connector")
    # Setelah missing_in_ref dan schema, year pasti int.
    if link["year"] > tahun_ini:
        raise ValidationError(
            f"Tahun tidak boleh melewati {tahun_ini}",
            code=YEAR_IN_FUTURE,
            field=f"{awalan}.year",
        )


def _connector_problem(posisi: int, penghubung: Any) -> str | None:
    """AC6: the first link has no connector, every next link uses jo or jis."""
    if posisi == 0 and penghubung != "none":
        return "Peraturan pertama tidak memakai penghubung"
    if posisi > 0 and penghubung == "none":
        return f"Peraturan ke-{posisi + 1} wajib disambung dengan jo. atau jis."
    return None


def _not_allowed(field: str, alasan: str) -> NoReturn:
    raise ValidationError(f"Field {field} {alasan}", code=FIELD_NOT_ALLOWED, field=field)


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
    """True when the field was omitted, sent as null, or blank after strip.

    A null year on an old Sprint 1 citation must read as "wajib diisi"
    (PBI-21, PO decision 17 Oct), not as a type error.
    """
    if str(error.get("type", "")) == "missing":
        return True
    if "input" in error and error["input"] is None:
        return True
    nilai = error.get("input")
    return isinstance(nilai, str) and not nilai.strip()


def _pydantic_message(error: dict[str, Any]) -> str:
    """Use Pydantic's message, or a generic one when that message is empty."""
    pesan = error.get("msg")
    if isinstance(pesan, str) and pesan.strip():
        return pesan
    return "Isian kasus tidak valid"
