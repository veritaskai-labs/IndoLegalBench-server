"""Body POST /admin/rubrics sintetis untuk tes PBI-14 (SCRUM-222 PR B).

Bentuknya meniru rubrik holistik: tiga dimensi tanpa bobot, level 0–5, dan satu
deskriptor per level per dimensi. Isinya netral; rubrik v1 klien tidak boleh masuk
repo publik (L2/L3), Admin mengetiknya sendiri di dev.
"""

from copy import deepcopy
from typing import Any

DIMENSI = ("Dimensi A", "Dimensi B", "Dimensi C")


def body_rubrik(**ubah: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "effective_date": "2026-10-27",
        "judge_prompt_quality": "Prompt kualitas sintetis.",
        "judge_prompt_format": "Prompt format sintetis.",
        "approved_by_name": None,
        "approved_at": None,
        "aspects": [
            {
                "name": nama,
                "grader_path": "llm",
                "weight": None,
                "definition": f"Definisi {nama} sintetis.",
            }
            for nama in DIMENSI
        ],
        "levels": [
            {
                "score": skor,
                "label": f"Level {skor}",
                "is_passing": skor >= 4,
                "descriptors": [
                    {"aspect_name": nama, "descriptor": f"{nama} pada level {skor}."}
                    for nama in DIMENSI
                ],
            }
            for skor in range(5, -1, -1)
        ],
    }
    body.update(deepcopy(ubah))
    return body
