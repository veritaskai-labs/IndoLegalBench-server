"""Penilai LLM. SCRUM-224. Pesan user untuk kualitas dan format."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

_QUALITY_LEAD = (
    "Score the quality of the provider answer against each criterion. "
    "Use only the criteria, the ground truth, and the question. "
    "A chain of legal references does not all have to be named."
)
_FORMAT_LEAD = (
    "Score whether the provider answer fits the format implied by the criteria "
    "and the ground truth. Use only those inputs. Do not apply an outside format standard."
)
_REPLY_RULES = (
    "Reply with one block per criterion and no other text.\n"
    "CRITERION: <criterion text>\n"
    "MET: yes\n"
    "or\n"
    "MET: no\n"
    "EXPLANATION: <one sentence>"
)
_UNMET_REASON = "unmet"
_SCORE_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class JudgeInputs:
    criteria: str
    ground_truth: str
    question: str
    provider_answer: str
    traps: str | None = None


def quality_prompt(inputs: JudgeInputs) -> str:
    return _prompt(_QUALITY_LEAD, inputs)


def format_prompt(inputs: JudgeInputs) -> str:
    return _prompt(_FORMAT_LEAD, inputs)


def _prompt(lead: str, inputs: JudgeInputs) -> str:
    body = (
        f"Criteria:\n{inputs.criteria}\n\n"
        f"Ground truth:\n{inputs.ground_truth}\n\n"
        f"Question:\n{inputs.question}\n\n"
        f"Provider answer:\n{inputs.provider_answer}\n\n"
        f"{_REPLY_RULES}"
    )
    return f"{lead}\n\n{body}"


@dataclass(frozen=True)
class CriterionJudgement:
    criterion: str
    met: bool
    explanation: str | None
    reason_id: str | None


@dataclass(frozen=True)
class JudgeRow:
    kind: str
    repetition: int
    prompt: str
    criteria: tuple[CriterionJudgement, ...] = ()
    score: Decimal | None = None
    explanation: str | None = None
    reason_id: str | None = None
    error: str | None = None
    failure_kind: str | None = None


@dataclass
class MemoryJudgeWriter:
    rows: list[JudgeRow] = field(default_factory=list)


def run_judge(
    asker: object,
    inputs: JudgeInputs,
    *,
    repetitions: int,
    writer: MemoryJudgeWriter | None = None,
) -> MemoryJudgeWriter:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    store = writer if writer is not None else MemoryJudgeWriter()
    for repetition in range(1, repetitions + 1):
        for kind, prompt in (
            ("quality", quality_prompt(inputs)),
            ("format", format_prompt(inputs)),
        ):
            response = asker.ask(prompt)
            failed = response.error is not None or response.failure_kind is not None
            criteria = () if failed else parse_reply(response.text)
            invalid = not failed and not criteria
            error = response.error
            failure_kind = response.failure_kind
            if invalid:
                error = "invalid_judge_reply"
                failure_kind = "invalid_response"
            explanation, reason_id = _summary(criteria)
            store.rows.append(
                JudgeRow(
                    kind=kind,
                    repetition=repetition,
                    prompt=prompt,
                    criteria=criteria,
                    score=None if failed or invalid else _score(criteria),
                    explanation=explanation,
                    reason_id=reason_id,
                    error=error,
                    failure_kind=failure_kind,
                )
            )
    return store


def parse_reply(text: str) -> tuple[CriterionJudgement, ...]:
    found: list[CriterionJudgement] = []
    criterion: str | None = None
    met: str | None = None
    explanation: str | None = None

    def close() -> None:
        nonlocal criterion, met, explanation
        if criterion is not None and met in ("yes", "no"):
            unmet = met == "no"
            found.append(
                CriterionJudgement(
                    criterion=criterion,
                    met=not unmet,
                    explanation=explanation if unmet else None,
                    reason_id=_UNMET_REASON if unmet else None,
                )
            )
        criterion = None
        met = None
        explanation = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.upper().startswith("CRITERION:"):
            close()
            criterion = line.split(":", 1)[1].strip()
        elif criterion is not None and line.upper().startswith("MET:"):
            met = line.split(":", 1)[1].strip().lower()
        elif criterion is not None and line.upper().startswith("EXPLANATION:"):
            explanation = line.split(":", 1)[1].strip() or None
    close()
    return tuple(found)


def _summary(criteria: Sequence[CriterionJudgement]) -> tuple[str | None, str | None]:
    unmet = [item for item in criteria if not item.met]
    if not unmet:
        return None, None
    sentences = [item.explanation for item in unmet if item.explanation]
    return (" ".join(sentences) if sentences else None), _UNMET_REASON


def _score(criteria: Sequence[CriterionJudgement]) -> Decimal:
    met = sum(1 for item in criteria if item.met)
    ratio = Decimal(met) / Decimal(len(criteria))
    return ratio.quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)
