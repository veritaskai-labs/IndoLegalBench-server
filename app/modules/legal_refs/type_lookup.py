"""Pencari code jenis peraturan dari teks bebas.

Rujukan Sprint 1 menyimpan jenis peraturan sebagai teks bebas, mis. "UU",
sedangkan tabel baru merujuk code. Teks dicocokkan ke code dulu, lalu
singkatan, lalu label, tanpa membedakan huruf besar atau kecil dan dengan
spasi dirapikan. Bila dua jenis berbagi teks yang sama, tingkat yang lebih
awal menang, dan di dalam satu tingkat jenis yang muncul lebih dulu menang.

Pemakai (pemeta rujukan lama) bergantung pada RegulationTypeLookup, bukan
pada kelas konkretnya, jadi test pemeta tidak perlu membaca seed.
"""

from collections.abc import Iterable
from typing import Protocol

from app.modules.legal_refs.seed import RegulationTypeSeed


class RegulationTypeLookup(Protocol):
    """Kemampuan mengubah teks jenis peraturan menjadi code, atau None."""

    def resolve(self, teks: str) -> str | None: ...


def _normalisasi(teks: str) -> str:
    return " ".join(teks.split()).casefold()


class RegulationTypeIndex:
    """Pencari dalam memori yang dibangun dari daftar jenis peraturan."""

    def __init__(self, jenis: Iterable[RegulationTypeSeed]) -> None:
        per_code: dict[str, str] = {}
        per_singkatan: dict[str, str] = {}
        per_label: dict[str, str] = {}
        for item in jenis:
            per_code.setdefault(_normalisasi(item.code), item.code)
            if item.singkatan:
                per_singkatan.setdefault(_normalisasi(item.singkatan), item.code)
            per_label.setdefault(_normalisasi(item.label), item.code)
        # Urutan tuple ini adalah urutan prioritas pencocokan.
        self._tingkat = (per_code, per_singkatan, per_label)

    def resolve(self, teks: str) -> str | None:
        kunci = _normalisasi(teks)
        if not kunci:
            return None
        for tingkat in self._tingkat:
            if kunci in tingkat:
                return tingkat[kunci]
        return None
