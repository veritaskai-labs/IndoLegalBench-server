"""Persen kasus selesai (PBI-12 AC5, SCRUM-265). Stub red: diisi di commit green."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseProgress:
    completed_cases: int
    total_cases: int
    percent_cases_complete: int


def case_progress(*, completed_cases: int, total_cases: int) -> CaseProgress:
    raise NotImplementedError
