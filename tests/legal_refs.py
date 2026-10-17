"""Rujukan hukum bentuk PBI-21 untuk fixture tes (SCRUM-160).

Fixture Sprint 1 menulis satu jenis, nomor, tahun, dan pasal per rujukan.
Sejak PBI-21 itu menjadi rujukan structured dengan satu link berbentuk pasal.
`rujukan_pasal` memakai nilai fixture lama (uu 13 tahun 2003), bukan contoh
baru. Contoh hukum yang boleh dipakai tes baru hanya KTK-001 dan AC10.
"""

from typing import Any


def link_pasal(
    pasal: str,
    *,
    regulation_type: str = "uu",
    number: str = "13",
    year: int = 2003,
    **lain: Any,
) -> dict[str, Any]:
    """Satu link berbentuk pasal, link pertama dalam rantai."""
    return {
        "form": "pasal",
        "connector": "none",
        "regulation_type": regulation_type,
        "number": number,
        "year": year,
        "pasal": pasal,
        **lain,
    }


def rujukan_pasal(pasal: str, **link: Any) -> dict[str, Any]:
    """Rujukan structured dengan satu link berbentuk pasal."""
    return {"kind": "structured", "links": [link_pasal(pasal, **link)]}


def rujukan_ktk_001() -> dict[str, Any]:
    """KTK-001 (Sprint 1): Pasal 156 UU 13/2003."""
    return rujukan_pasal("156")


def rujukan_ac10() -> dict[str, Any]:
    """Contoh AC10 dari Aileen, belum dikonfirmasi tim legal.

    "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023".
    """
    return {
        "kind": "structured",
        "links": [
            link_pasal("156"),
            {
                "form": "pasal_angka",
                "connector": "jo",
                "regulation_type": "uu",
                "number": "6",
                "year": 2023,
                "pasal": "II",
                "angka": "5",
            },
        ],
    }
