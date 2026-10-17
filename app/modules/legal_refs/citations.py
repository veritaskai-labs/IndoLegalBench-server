"""Pembaca rujukan hukum dari case_versions.content (PBI-21 AC9, SCRUM-160).

content["legal_refs"] berisi bentuk baru (kind/links) untuk versi yang disimpan
editor PBI-21, dan bentuk Sprint 1 untuk versi lama yang tidak ikut berubah.
Fungsi di sini selalu mengembalikan bentuk baru.

Bentuk dideteksi dulu: hanya entri lama yang diteruskan ke map_legacy_refs,
karena pemeta itu melewati entri bentuk baru tanpa jejak. Aturan pemetaannya
sama persis dengan migrasi SCRUM-159.
"""

from typing import Any

from app.modules.legal_refs.legacy_mapping import LegalRefDraft, map_legacy_refs
from app.modules.legal_refs.type_lookup import RegulationTypeLookup


def to_citations(raw: Any, lookup: RegulationTypeLookup) -> list[dict[str, Any]]:
    """Daftar rujukan dalam bentuk PBI-21, urutan tetap. Bukan daftar berarti tanpa rujukan."""
    if not isinstance(raw, list):
        return []
    hasil: list[dict[str, Any]] = []
    for entri in raw:
        if _bentuk_baru(entri):
            hasil.append(entri)
            continue
        hasil.extend(_dari_draf(draf) for draf in map_legacy_refs([entri], lookup).refs)
    return hasil


def _bentuk_baru(entri: Any) -> bool:
    return isinstance(entri, dict) and "kind" in entri


def _dari_draf(draf: LegalRefDraft) -> dict[str, Any]:
    """Draf pemeta 159 sebagai dict bentuk PBI-21. Field yang tidak ada di Sprint 1 bernilai None."""
    return {
        "kind": draf.kind.value,
        "free_text": draf.free_text,
        "links": [
            {
                "form": link.form.value,
                "connector": link.connector.value,
                "regulation_type": link.regulation_type,
                "number": link.number,
                "year": link.year,
                "about": None,
                "pasal": link.pasal,
                "angka": None,
                "ayat": link.ayat,
                "huruf": link.huruf,
            }
            for link in draf.links
        ],
    }
