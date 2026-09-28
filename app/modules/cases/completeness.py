from typing import Any, Protocol


class CaseLike(Protocol):
    """Bentuk minimal yang dibutuhkan, dipenuhi model ORM maupun schema."""

    title: str | None
    question: str | None
    category: str | None
    legal_refs: list[dict[str, Any]] | None
    answer_criteria: dict[str, Any] | None
    traps: list[dict[str, Any]] | None
    split_tag: str | None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _count_refs_with_pasal(legal_refs: list[dict[str, Any]] | None) -> int:
    """Rujukan baru dihitung kalau sudah turun sampai pasal."""
    if not legal_refs:
        return 0
    return sum(1 for ref in legal_refs if not _is_blank(ref.get("pasal")))


def _count_criteria(answer_criteria: dict[str, Any] | None) -> int:
    if not answer_criteria:
        return 0

    count = len(answer_criteria.get("must_contain") or [])
    count += len(answer_criteria.get("must_not_contain") or [])
    if not _is_blank(answer_criteria.get("expected_conclusion")):
        count += 1
    return count


def evaluate(case: CaseLike) -> dict[str, Any]:
    """Kembalikan ringkasan kelengkapan, siap disimpan ke kolom JSONB."""
    missing: list[dict[str, str]] = []

    if _is_blank(case.title):
        missing.append({"field": "identity.title", "message": "Judul wajib diisi."})

    if _is_blank(case.question):
        missing.append(
            {"field": "identity.question", "message": "Pertanyaan wajib diisi."}
        )

    if _is_blank(case.category):
        missing.append(
            {"field": "identity.category", "message": "Kategori wajib diisi."}
        )

    legal_ref_count = _count_refs_with_pasal(case.legal_refs)
    if legal_ref_count == 0:
        missing.append(
            {
                "field": "legal_refs",
                "message": "Butuh minimal satu rujukan hukum sampai tingkat pasal.",
            }
        )

    if _count_criteria(case.answer_criteria) == 0:
        missing.append(
            {
                "field": "answer_criteria",
                "message": "Butuh minimal satu kriteria jawaban.",
            }
        )

    trap_count = len(case.traps or [])
    if trap_count == 0:
        missing.append({"field": "traps", "message": "Butuh minimal satu jebakan."})

    if _is_blank(case.split_tag):
        missing.append(
            {"field": "split_tag", "message": "Tag dev atau test wajib dipilih."}
        )

    is_complete = len(missing) == 0

    return {
        "is_complete": is_complete,
        # ready_for_review sama dengan is_complete, termasuk syarat minimal
        # satu jebakan. Dipakai sebagai syarat ajukan review di PBI-6.
        "ready_for_review": is_complete,
        "missing": missing,
        "trap_count": trap_count,
        "legal_ref_count": legal_ref_count,
    }