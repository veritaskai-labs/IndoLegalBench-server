"""Identitas kasus satu sesi, dibaca dari snapshot. PBI-14 AC10, SCRUM-225.

Pecahan per kategori harus memakai kategori yang tersimpan di snapshot sesi,
bukan kategori kasus yang sekarang. Modul ini tidak membaca database.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.modules.cases.models import SplitTag
from app.modules.runs.score_aggregation import CaseScore, ScorePair


@dataclass(frozen=True)
class CaseFacts:
    snapshot_item_id: uuid.UUID
    case_code: str
    split_tag: SplitTag
    category: str | None


class SnapshotCasePort(Protocol):
    """Port untuk membaca identitas kasus dari snapshot."""

    def case_facts(self, session_id: uuid.UUID) -> tuple[CaseFacts, ...]:
        """Semua kasus dalam sesi, dengan urutan stabil."""


def case_facts_from_body(
    snapshot_item_id: uuid.UUID,
    body: Mapping[str, Any] | None,
) -> CaseFacts:
    """Petakan body snapshot menjadi identitas kasus."""
    bagian = (body or {}).get("sections") or {}
    mentah = bagian.get("split_tag")

    try:
        split_tag = SplitTag(mentah)
    except ValueError as galat:
        raise ValueError(
            f"split_tag snapshot tidak dikenal untuk item {snapshot_item_id}: {mentah!r}"
        ) from galat

    kategori = bagian.get("category")
    bersih = kategori.strip() if isinstance(kategori, str) else None

    return CaseFacts(
        snapshot_item_id=snapshot_item_id,
        case_code=str(bagian.get("case_code") or ""),
        split_tag=split_tag,
        category=bersih or None,
    )


def build_case_scores(
    facts: Sequence[CaseFacts],
    scores: Mapping[uuid.UUID, ScorePair],
) -> tuple[CaseScore, ...]:
    """Pasangkan identitas dari snapshot dengan skor berdasarkan item id."""
    kosong = ScorePair(deterministic=None, judge=None)

    return tuple(
        CaseScore(
            case_code=fakta.case_code,
            split_tag=fakta.split_tag,
            category=fakta.category,
            scores=scores.get(fakta.snapshot_item_id, kosong),
        )
        for fakta in facts
    )
