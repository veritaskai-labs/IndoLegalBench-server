"""Penilai LLM. SCRUM-224. Pesan user untuk kualitas dan format."""

from dataclasses import dataclass, field

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
class JudgeRow:
    kind: str
    repetition: int
    prompt: str


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
            asker.ask(prompt)
            store.rows.append(JudgeRow(kind=kind, repetition=repetition, prompt=prompt))
    return store
