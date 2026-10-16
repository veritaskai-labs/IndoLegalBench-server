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
# PBI-21: field wajib setiap link, lalu tambahan menurut bentuk link.
FIELD_LINK = ("regulation_type", "number", "year")
FIELD_PER_BENTUK = {"name_only": (), "pasal": ("pasal",), "pasal_angka": ("pasal", "angka")}
KIND_RUJUKAN = frozenset({"structured", "free_text"})

# PBI-21 AC5: pasal angka biasa atau Romawi yang sah (huruf besar). Akhiran
# satu huruf besar ("1A", pasal sisipan) usulan PO, menunggu konfirmasi Steven.
# Tanpa lookaround supaya sama di regex Pydantic (Rust) dan zod (JavaScript).
POLA_PASAL = (
    r"^(?:[1-9][0-9]*[A-Z]?"
    r"|M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))$"
)
POLA_ANGKA = r"^[1-9][0-9]*$"

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


def missing_in_ref(rujukan: Any) -> str | None:
    """Jalur relatif field pertama yang kosong di satu rujukan, atau None kalau lengkap.

    "" berarti rujukannya sendiri bukan objek. Contoh lain: ".kind",
    ".free_text", ".links", ".links[1]", ".links[0].pasal". Pemanggil
    menambahkan awalan "legal_refs[i]".
    """
    if not isinstance(rujukan, dict):
        return ""
    jenis = rujukan.get("kind")
    if jenis not in KIND_RUJUKAN:
        return ".kind"
    if jenis == "free_text":
        return None if _text(rujukan.get("free_text")) else ".free_text"
    links = rujukan.get("links")
    if not isinstance(links, list) or not links:
        return ".links"
    for posisi, link in enumerate(links):
        kurang = _missing_in_link(link)
        if kurang is not None:
            return f".links[{posisi}]{kurang}"
    return None


def _missing_in_link(link: Any) -> str | None:
    """Jalur relatif field pertama yang kosong di satu link, atau None kalau lengkap."""
    if not isinstance(link, dict):
        return ""
    bentuk = link.get("form")
    if bentuk not in FIELD_PER_BENTUK:
        return ".form"
    for nama in FIELD_LINK + FIELD_PER_BENTUK[bentuk]:
        if not _text(link.get(nama)):
            return f".{nama}"
    return None


def _invalid_refs(legal_refs: Any) -> list[dict[str, str]]:
    """Satu masalah per rujukan rusak, menyebut field pertama yang kosong."""
    masalah: list[dict[str, str]] = []
    for indeks, rujukan in enumerate(legal_refs or []):
        kurang = missing_in_ref(rujukan)
        if kurang is not None:
            masalah.append({"field": f"legal_refs[{indeks}]{kurang}", "message": _PESAN_RUJUKAN})
    return masalah


def _count_refs(legal_refs: Any) -> int:
    """Rujukan dihitung kalau kind dan isi wajib setiap link-nya terpenuhi."""
    if not isinstance(legal_refs, list):
        return 0
    return sum(1 for rujukan in legal_refs if missing_in_ref(rujukan) is None)


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
