"""Rumus kelengkapan kasus (SCRUM-107).

Aturan dasar kasus (pola case_code, tag sah, field rujukan) hanya ditulis di
sini. validation.py mengimpor modul ini, bukan sebaliknya, supaya tidak ada
impor melingkar dan tidak ada salinan aturan.
"""

import re
from typing import Any

# TODO(Klarifikasi #7): pola case_code masih sementara. PHK-001 dan phk-001
# sama-sama lolos. Ganti di sini saja; validation.py ikut memakainya.

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
)

_PESAN = {
    "identity.title": "Judul wajib diisi.",
    "identity.question": "Pertanyaan wajib diisi.",
    "case_code": "Kode kasus wajib diisi dan harus sesuai pola.",
    "split_tag": "Tag dev atau test wajib dipilih.",
    "legal_refs": "Butuh minimal satu rujukan hukum sampai tingkat pasal.",
    "answer_criteria": "Butuh minimal satu kriteria jawaban.",
}
_PESAN_RUJUKAN = "Rujukan hukum harus lengkap sampai pasal."


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


def review_blockers(data: dict[str, Any]) -> list[dict[str, str]]:
    """Semua alasan kasus belum boleh diajukan review (SCRUM-143, guard D2).

    Bagian yang kurang dari evaluate(), lalu setiap rujukan yang belum
    lengkap. evaluate() cukup dengan satu rujukan sah, sedangkan pengajuan
    menolak bila ada satu pun rujukan yang rusak.
    """
    hasil = evaluate(data)
    blockers = list(hasil["missing"])
    if hasil["legal_ref_count"] > 0:
        blockers.extend(_invalid_refs(data.get("legal_refs")))
    return blockers


def from_row(case: Any) -> dict[str, Any]:
    """Hitung ulang dari versi yang sedang dikerjakan, bukan dari salinan tersimpan.

    Isi ada di `current_version.content`. Kode kasus tetap di baris identitas.
    """
    return evaluate(row_data(case))


def row_data(case: Any) -> dict[str, Any]:
    """Bentuk body kasus dari versi yang sedang dikerjakan."""
    versi = case.current_version
    isi = versi.content or {}
    tag = versi.split_tag
    return {
        "case_code": case.case_code,
        "identity": {"title": isi.get("title"), "question": isi.get("question")},
        "legal_refs": isi.get("legal_refs") or [],
        "answer_criteria": isi.get("answer_criteria") or {},
        "traps": isi.get("traps") or [],
        "split_tag": str(tag) if tag else None,
    }


def _invalid_refs(legal_refs: Any) -> list[dict[str, str]]:
    """Satu masalah per rujukan rusak, menyebut field pertama yang kosong."""
    masalah: list[dict[str, str]] = []
    for indeks, rujukan in enumerate(legal_refs or []):
        if not isinstance(rujukan, dict):
            masalah.append({"field": f"legal_refs[{indeks}]", "message": _PESAN_RUJUKAN})
            continue
        kosong = [nama for nama in FIELD_RUJUKAN if not _text(rujukan.get(nama))]
        if kosong:
            masalah.append(
                {"field": f"legal_refs[{indeks}].{kosong[0]}", "message": _PESAN_RUJUKAN}
            )
    return masalah


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
