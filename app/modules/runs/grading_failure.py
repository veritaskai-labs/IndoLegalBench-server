"""Kode alasan sebuah grading run gagal. SCRUM-78 SCRUM-289 (K-19)."""

from collections.abc import Iterable

NO_SCORE = "NO_SCORE"
GRADING_ERROR = "GRADING_ERROR"
JUDGE_PRICING_MISSING = "judge_pricing_missing"


def resolve_failure_code(
    judge_errors: Iterable[str | None], *, pricing_missing: bool = False
) -> str:
    """Satu kode untuk run yang gagal, dari galat judge tiap percobaannya."""
    return NO_SCORE
