"""Penilai LLM. SCRUM-224. Pesan user untuk kualitas dan format."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.modules.runs.call_cost import actual_cost_idr

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
class JudgePrices:
    usd_per_million_input_tokens: Decimal | None
    usd_per_million_output_tokens: Decimal | None
    usd_idr_rate: Decimal


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
    cost_idr: Decimal | None = None


@dataclass
class MemoryJudgeWriter:
    rows: list[JudgeRow] = field(default_factory=list)


def run_judge(
    asker: object,
    inputs: JudgeInputs,
    *,
    repetitions: int,
    prices: JudgePrices | None = None,
    writer: MemoryJudgeWriter | None = None,
) -> MemoryJudgeWriter:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    store = writer if writer is not None else MemoryJudgeWriter()
    expected = _criterion_names(inputs.criteria)
    for repetition in range(1, repetitions + 1):
        for kind, prompt in (
            ("quality", quality_prompt(inputs)),
            ("format", format_prompt(inputs)),
        ):
            store.rows.append(
                _row(
                    asker.ask(prompt),
                    kind=kind,
                    repetition=repetition,
                    prompt=prompt,
                    prices=prices,
                    expected=expected,
                )
            )
    return store


def _row(
    response: object,
    *,
    kind: str,
    repetition: int,
    prompt: str,
    prices: JudgePrices | None,
    expected: tuple[str, ...],
) -> JudgeRow:
    failed = response.error is not None or response.failure_kind is not None
    parsed = () if failed else parse_reply(response.text)
    aligned = None if failed else _align(expected, parsed)
    error = response.error
    failure_kind = response.failure_kind
    if _rejected(failed, aligned):
        error = "invalid_judge_reply"
        failure_kind = "invalid_response"
        failed = True
    criteria = aligned if aligned is not None else ()
    explanation, reason_id = _summary(criteria)
    return JudgeRow(
        kind=kind,
        repetition=repetition,
        prompt=prompt,
        criteria=criteria,
        score=None if failed else _score(criteria, len(expected)),
        explanation=explanation,
        reason_id=reason_id,
        error=error,
        failure_kind=failure_kind,
        cost_idr=_cost(response, prices),
    )


def _rejected(failed: bool, aligned: tuple[CriterionJudgement, ...] | None) -> bool:
    return not failed and aligned is None


def _criterion_names(criteria: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in criteria.splitlines() if line.strip())


def _align(
    expected: tuple[str, ...], found: tuple[CriterionJudgement, ...]
) -> tuple[CriterionJudgement, ...] | None:
    if not expected:
        return None
    by_name: dict[str, CriterionJudgement] = {}
    for item in found:
        if item.criterion in by_name or item.criterion not in expected:
            return None
        by_name[item.criterion] = item
    if len(by_name) != len(expected):
        return None
    return tuple(by_name[name] for name in expected)


@dataclass
class _ReplyCursor:
    criterion: str | None = None
    met: str | None = None
    explanation: str | None = None

    def clear(self) -> None:
        self.criterion = None
        self.met = None
        self.explanation = None


def parse_reply(text: str) -> tuple[CriterionJudgement, ...]:
    found: list[CriterionJudgement] = []
    cursor = _ReplyCursor()
    for raw_line in text.splitlines():
        _read_line(cursor, found, raw_line.strip())
    _close_block(cursor, found)
    return tuple(found)


def _read_line(cursor: _ReplyCursor, found: list[CriterionJudgement], line: str) -> None:
    plain = line.replace("*", "").strip()
    criterion = _labeled(plain, "CRITERION")
    if criterion is not None:
        _close_block(cursor, found)
        cursor.criterion = criterion
        return
    if cursor.criterion is None:
        return
    met = _labeled(plain, "MET")
    if met is not None:
        cursor.met = _met_token(met)
        return
    explanation = _labeled(plain, "EXPLANATION")
    if explanation is not None:
        cursor.explanation = explanation or None


def _met_token(raw: str) -> str:
    token = raw.strip()
    while token and token[-1] in ".,;:!?":
        token = token[:-1].strip()
    return token.lower()


def _close_block(cursor: _ReplyCursor, found: list[CriterionJudgement]) -> None:
    met = cursor.met
    if cursor.criterion is None or met not in ("yes", "no"):
        cursor.clear()
        return
    unmet = met == "no"
    found.append(
        CriterionJudgement(
            criterion=cursor.criterion,
            met=not unmet,
            explanation=cursor.explanation if unmet else None,
            reason_id=_UNMET_REASON if unmet else None,
        )
    )
    cursor.clear()


def _labeled(line: str, label: str) -> str | None:
    if line.upper().startswith(f"{label}:"):
        return line.split(":", 1)[1].strip()
    return None


def _summary(criteria: Sequence[CriterionJudgement]) -> tuple[str | None, str | None]:
    unmet = [item for item in criteria if not item.met]
    if not unmet:
        return None, None
    sentences = [item.explanation for item in unmet if item.explanation]
    return (" ".join(sentences) if sentences else None), _UNMET_REASON


def _score(criteria: Sequence[CriterionJudgement], total: int) -> Decimal:
    met = sum(1 for item in criteria if item.met)
    ratio = Decimal(met) / Decimal(total)
    return ratio.quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def _cost(response: object, prices: JudgePrices | None) -> Decimal | None:
    if prices is None:
        return None
    return actual_cost_idr(
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        usd_per_million_input_tokens=prices.usd_per_million_input_tokens,
        usd_per_million_output_tokens=prices.usd_per_million_output_tokens,
        usd_idr_rate=prices.usd_idr_rate,
    )
