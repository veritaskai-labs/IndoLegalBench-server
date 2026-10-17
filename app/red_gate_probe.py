"""Throwaway module for SCRUM-174: proves a red SonarQube gate blocks a PR.

Never merged. It has no tests on purpose, so coverage on new code drops
below the gate threshold.
"""


def classify(score: int) -> str:
    if score < 0:
        return "invalid"
    if score < 50:
        return "low"
    if score < 75:
        return "medium"
    if score < 90:
        return "high"
    return "top"


def summarize(scores: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for score in scores:
        label = classify(score)
        counts[label] = counts.get(label, 0) + 1
    return counts


def average(scores: list[int]) -> float:
    if not scores:
        return 0.0
    return sum(scores) / len(scores)
