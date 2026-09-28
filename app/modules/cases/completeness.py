import re
from typing import Any

# Disalin dari validation.py
# validation.py mengimpor modul ini jadi cannot import the validation.py here

# TODO(Klarifikasi #7): pola case_code masih sementara, samakan dengan
# validation.PLACEHOLDER_CASE_CODE_PATTERN kalau pola finalnya sudah ada.

PLACEHOLDER_CASE_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$"
TAG_SAH = frozenset({"dev", "test"})
FIELD_RUJUKAN = ("regulation_type", "regulation_number", "pasal")

_POLA_KODE = re.compile(PLACEHOLDER_CASE_CODE_PATTERN)


# Urutan ini menentukan urutan `missing` dan pembagi `pct`.
_BAGIAN = (
    "identity.title",
    "identity.question",
    "case_code",
    "split_tag",
    "legal_refs",
    "answer_criteria",
    "traps",
)

_PESAN = {
    "identity.title": "Judul wajib diisi.",
    "identity.question": "Pertanyaan wajib diisi.",
    "case_code": "Kode kasus wajib diisi dan harus sesuai pola.",
    "split_tag": "Tag dev atau test wajib dipilih.",
    "legal_refs": "Butuh minimal satu rujukan hukum sampai tingkat pasal.",
    "answer_criteria": "Butuh minimal satu kriteria jawaban.",
    "traps": "Butuh minimal satu jebakan sebelum kasus bisa diajukan review.",
}


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    """Hitung kelengkapan dari body kasus, siap disimpan ke kolom JSONB."""
    identitas = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    legal_ref_count = _count_refs(data.get("legal_refs"))
    trap_count = _count_traps(data.get("traps"))

    terisi = {
        "identity.title": bool(_text(identitas.get("title"))),
        "identity.question": bool(_text(identitas.get("question"))),
        "case_code": _POLA_KODE.fullmatch(_text(data.get("case_code"))) is not None,
        "split_tag": data.get("split_tag") in TAG_SAH,
        "legal_refs": legal_ref_count > 0,
        "answer_criteria": _has_answer_criteria(data.get("answer_criteria")),
        "traps": trap_count > 0,
    }

    missing = [{"field": nama, "message": _PESAN[nama]} for nama in _BAGIAN if not terisi[nama]]
    is_complete = not missing

    return {
        "is_complete": is_complete,
        "ready_for_review": is_complete,
        "missing": missing,
        "trap_count": trap_count,
        "legal_ref_count": legal_ref_count,
        # Dipakai daftar kasus lewat CaseSummary.completeness_pct.
        "pct": round((len(_BAGIAN) - len(missing)) * 100 / len(_BAGIAN)),
    }


def from_row(case: Any) -> dict[str, Any]:
    """Hitung ulang dari baris database, untuk baris lama yang belum disimpan ulang."""
    return evaluate(
        {
            "case_code": case.case_code,
            "identity": {"title": case.title, "question": case.question},
            "legal_refs": case.legal_refs or [],
            "answer_criteria": case.answer_criteria or {},
            "traps": case.traps or [],
            "split_tag": str(case.split_tag) if case.split_tag else None,
        }
    )


def _count_refs(legal_refs: Any) -> int:
    """Rujukan baru dihitung kalau tipe, nomor, dan pasal sudah terisi."""
    if not isinstance(legal_refs, list):
        return 0
    return sum(
        1
        for rujukan in legal_refs
        if isinstance(rujukan, dict) and all(_text(rujukan.get(nama)) for nama in FIELD_RUJUKAN)
    )


def _count_traps(traps: Any) -> int:
    """Jebakan baru dihitung kalau deskripsinya terisi."""
    if not isinstance(traps, list):
        return 0
    return sum(1 for item in traps if isinstance(item, dict) and _text(item.get("description")))


def _has_answer_criteria(nilai: Any) -> bool:
    """True kalau ada frasa wajib, frasa terlarang, atau kesimpulan."""
    if not isinstance(nilai, dict):
        return False
    for kunci in ("must_contain", "must_not_contain"):
        butir = nilai.get(kunci) or []
        if isinstance(butir, list) and any(_text(item) for item in butir):
            return True
    return bool(_text(nilai.get("expected_conclusion")))


def _text(nilai: Any) -> str:
    """Kembalikan teks yang sudah dipangkas, atau string kosong untuk None."""
    if nilai is None:
        return ""
    return str(nilai).strip()
