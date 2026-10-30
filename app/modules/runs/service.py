"""Logika bisnis modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

TODO(PBI-11, PBI-12, PBI-13): implementasikan sesuai acceptance criteria.
"""

from collections.abc import Mapping

from app.modules.runs.schemas import CaseFilter

# Split tag yang dihitung untuk tiap pilihan. Pilihan baru cukup menambah baris.
_SPLITS_FOR_FILTER: Mapping[CaseFilter, tuple[str, ...]] = {
    CaseFilter.DEV: ("dev",),
    CaseFilter.TEST: ("test",),
    CaseFilter.BOTH: ("dev", "test"),
}


def eligible_case_count(counts: Mapping[str, int], case_filter: CaseFilter) -> int:
    """Jumlah kasus approved untuk pilihan dev, test, atau both (PBI-11 AC2).

    counts berkunci nama split tag. Split yang tidak ada dianggap nol.
    """
    return sum(counts.get(split, 0) for split in _SPLITS_FOR_FILTER[case_filter])


def total_calls(case_count: int, repeat_count: int) -> int:
    """Total panggilan ke produk AI: kasus kali pengulangan (PBI-11 AC3)."""
    return case_count * repeat_count
