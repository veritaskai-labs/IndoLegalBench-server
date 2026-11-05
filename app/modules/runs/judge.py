"""Penilai LLM untuk kualitas dan kesesuaian format. SCRUM-224.

Instruksi ada di satu pesan user. Tidak ada system prompt. Hasil disimpan
di memori sampai tabel SCRUM-222 ada. Gangguan teknis milik SCRUM-226.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from app.modules.providers.adapters.base import ProviderResponse
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
_INVALID_REPLY = "invalid_judge_reply"
_SCORE_QUANTUM = Decimal("0.01")


class JudgeAsker(Protocol):
    """Cukup ask(). Adapter sungguhan dan mock judge sama-sama memenuhi ini."""

    def ask(
        self, prompt: str, *, parameters: object = None, timeout: float = 0
    ) -> ProviderResponse:
        """Kirim satu pesan user."""


@dataclass(frozen=True)
class JudgeInputs:
    """Empat masukan tiket. traps diterima lalu diabaikan."""

    criteria: str
    ground_truth: str
    question: str
    provider_answer: str
    traps: str | None = None


@dataclass(frozen=True)
class JudgePrices:
    """Harga dan kurs untuk biaya satu baris. Kosong berarti biaya tidak dihitung."""

    usd_per_million_input_tokens: Decimal | None
    usd_per_million_output_tokens: Decimal | None
    usd_idr_rate: Decimal


@dataclass(frozen=True)
class CriterionJudgement:
    """Satu kriteria: terpenuhi atau tidak, plus penjelasan bila tidak."""

    criterion: str
    met: bool
    explanation: str | None
    reason_id: str | None


@dataclass(frozen=True)
class JudgeRow:
    """Satu panggilan penilai. Skor kosong bila panggilan gagal atau balasan tidak sah."""

    kind: str
    repetition: int
    prompt: str
    criteria: tuple[CriterionJudgement, ...]
    score: Decimal | None
    explanation: str | None
    reason_id: str | None
    error: str | None
    failure_kind: str | None
    cost_idr: Decimal | None


@dataclass
class MemoryJudgeWriter:
    """Penyimpan stub. Bukan tabel judge_results."""

    rows: list[JudgeRow] = field(default_factory=list)

    def add(self, row: JudgeRow) -> None:
        self.rows.append(row)


def quality_prompt(inputs: JudgeInputs) -> str:
    """Pesan user untuk kualitas."""
    return _prompt(_QUALITY_LEAD, inputs)


def format_prompt(inputs: JudgeInputs) -> str:
    """Pesan user untuk kesesuaian format."""
    return _prompt(_FORMAT_LEAD, inputs)


def run_judge(
    asker: JudgeAsker,
    inputs: JudgeInputs,
    *,
    repetitions: int,
    prices: JudgePrices | None = None,
    writer: MemoryJudgeWriter | None = None,
) -> MemoryJudgeWriter:
    """Jalankan kualitas dan format sebanyak pengulangan. Tulis setiap baris ke stub."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    store = writer if writer is not None else MemoryJudgeWriter()
    for repetition in range(1, repetitions + 1):
        for kind, prompt in (
            ("quality", quality_prompt(inputs)),
            ("format", format_prompt(inputs)),
        ):
            response = asker.ask(prompt)
            store.add(_row(kind, repetition, prompt, response, prices))
    return store


def parse_reply(text: str) -> tuple[CriterionJudgement, ...]:
    """Blok CRITERION/MET. Balasan tanpa blok yang sah menghasilkan tuple kosong."""
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


def _prompt(lead: str, inputs: JudgeInputs) -> str:
    body = (
        f"Criteria:\n{inputs.criteria}\n\n"
        f"Ground truth:\n{inputs.ground_truth}\n\n"
        f"Question:\n{inputs.question}\n\n"
        f"Provider answer:\n{inputs.provider_answer}\n\n"
        f"{_REPLY_RULES}"
    )
    return f"{lead}\n\n{body}"


def _row(
    kind: str,
    repetition: int,
    prompt: str,
    response: ProviderResponse,
    prices: JudgePrices | None,
) -> JudgeRow:
    failed = response.error is not None or response.failure_kind is not None
    criteria: Sequence[CriterionJudgement] = () if failed else parse_reply(response.text)
    invalid = not failed and not criteria
    error = response.error
    failure_kind = response.failure_kind
    if invalid:
        error = _INVALID_REPLY
        failure_kind = failure_kind or "invalid_response"
        criteria = ()
    score = None if failed or invalid else _score(criteria)
    explanation, reason_id = _summary(criteria)
    return JudgeRow(
        kind=kind,
        repetition=repetition,
        prompt=prompt,
        criteria=tuple(criteria),
        score=score,
        explanation=explanation,
        reason_id=reason_id,
        error=error,
        failure_kind=failure_kind,
        cost_idr=_cost(response, prices),
    )


def _summary(criteria: Sequence[CriterionJudgement]) -> tuple[str | None, str | None]:
    unmet = [item for item in criteria if not item.met]
    if not unmet:
        return None, None
    sentences = [item.explanation for item in unmet if item.explanation]
    explanation = " ".join(sentences) if sentences else None
    return explanation, _UNMET_REASON


def _score(criteria: Sequence[CriterionJudgement]) -> Decimal:
    met = sum(1 for item in criteria if item.met)
    ratio = Decimal(met) / Decimal(len(criteria))
    return ratio.quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def _cost(response: ProviderResponse, prices: JudgePrices | None) -> Decimal | None:
    if prices is None:
        return None
    return actual_cost_idr(
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        usd_per_million_input_tokens=prices.usd_per_million_input_tokens,
        usd_per_million_output_tokens=prices.usd_per_million_output_tokens,
        usd_idr_rate=prices.usd_idr_rate,
    )
