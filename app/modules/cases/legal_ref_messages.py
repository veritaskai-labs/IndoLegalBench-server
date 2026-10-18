"""Teks pesan error rujukan hukum (PBI-21, SCRUM-162).

Keputusan PO 18 Okt: kalimat biasa dengan label yang sama seperti form
editor, supaya simpan dan pratinjau memakai satu bahasa error (Design
hal. 24). Hanya teksnya yang diatur di sini; kode dan path field tetap,
karena client memetakan error lewat path.
"""

import re
from typing import Any

# Sama dengan FIELD_LABELS di client (src/lib/cases/caseValidation.ts),
# supaya pesan zod di browser dan pesan server berbunyi sama.
LABEL_FIELD = {
    "kind": "Bentuk rujukan",
    "form": "Bentuk peraturan",
    "connector": "Penghubung",
    "regulation_type": "Jenis peraturan",
    "number": "Nomor peraturan",
    "year": "Tahun",
    "about": "Tentang",
    "pasal": "Pasal",
    "angka": "Angka",
    "ayat": "Ayat",
    "huruf": "Huruf",
    "free_text": "Teks bebas",
}
# Nama bentuk link seperti di editor (AC4).
LABEL_BENTUK = {"name_only": "Nama Peraturan", "pasal": "Pasal", "pasal_angka": "Pasal + Angka"}

LINKS_ON_FREE_TEXT = "Teks Bebas tidak boleh berisi peraturan."
FREE_TEXT_ON_STRUCTURED = "Teks bebas hanya untuk bentuk Teks Bebas."
_TANPA_PERATURAN = "Rujukan terstruktur butuh minimal satu peraturan."

_RUJUKAN = re.compile(r"legal_refs\[(\d+)\](?:\.(\w+))?")
_LINK = re.compile(r"legal_refs\[\d+\]\.links\[(\d+)\](?:\.(\w+))?")


def required(field: str, rujukan: Any = None) -> str | None:
    """Pesan FIELD_REQUIRED untuk path di dalam satu rujukan, atau None di luar itu.

    `rujukan` adalah isi rujukan yang ditolak. Kalau ada, pesan pasal dan
    angka menyebut bentuk link yang salah, misalnya "untuk bentuk Pasal".
    """
    link = _LINK.fullmatch(field)
    if link is not None:
        return _link_required(int(link[1]), link[2], rujukan)
    isi = _RUJUKAN.fullmatch(field)
    if isi is None:
        return None
    nama = isi[2]
    if nama is None:
        return f"Rujukan ke-{int(isi[1]) + 1} wajib diisi."
    if nama == "links":
        if isinstance(rujukan, dict) and rujukan.get("kind") == "structured":
            return _TANPA_PERATURAN
        return "Peraturan wajib diisi."
    return _label_required(nama)


def not_allowed_in_form(nama: str, bentuk: str) -> str:
    """Pesan FIELD_NOT_ALLOWED untuk field link yang tidak dipakai bentuknya."""
    return f"{LABEL_FIELD[nama]} tidak boleh diisi untuk bentuk {LABEL_BENTUK[bentuk]}."


def _link_required(posisi: int, nama: str | None, rujukan: Any) -> str | None:
    if nama is None:
        return f"Peraturan ke-{posisi + 1} wajib diisi."
    if nama in ("pasal", "angka") and isinstance(rujukan, dict):
        # Hanya aturan simpan yang melaporkan pasal atau angka kosong, dan itu
        # terjadi setelah bentuk link dipastikan sah (missing_in_ref).
        bentuk = LABEL_BENTUK[rujukan["links"][posisi]["form"]]
        return f"{LABEL_FIELD[nama]} wajib diisi untuk bentuk {bentuk}."
    return _label_required(nama)


def _label_required(nama: str) -> str | None:
    """Pesan "{Label} wajib diisi." untuk field yang dikenal form editor."""
    label = LABEL_FIELD.get(nama)
    return None if label is None else f"{label} wajib diisi."
