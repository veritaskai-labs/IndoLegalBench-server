"""Penilai LLM. SCRUM-224. Stub: prompt masih kosong."""

from dataclasses import dataclass


@dataclass(frozen=True)
class JudgeInputs:
    criteria: str
    ground_truth: str
    question: str
    provider_answer: str
    traps: str | None = None


def quality_prompt(inputs: JudgeInputs) -> str:
    del inputs
    return ""


def format_prompt(inputs: JudgeInputs) -> str:
    del inputs
    return ""
