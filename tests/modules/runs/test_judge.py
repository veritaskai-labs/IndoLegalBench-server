"""Pesan user penilai. SCRUM-224."""

from app.modules.providers.adapters.base import ProviderResponse
from app.modules.runs.judge import (
    JudgeInputs,
    format_prompt,
    quality_prompt,
    run_judge,
)

_INPUTS = JudgeInputs(
    criteria="mentions Pasal 156\ncites UU 40/2007\nstates the conclusion\nnames the parties",
    ground_truth="Pasal 156 UU 40/2007",
    question="Apa akibat hukumnya?",
    provider_answer="Jawaban provider.",
    traps="jebakan rahasia yang tidak boleh masuk prompt",
)


def test_pesan_user_tanpa_system_prompt():
    """Negatif: instruksi tinggal di pesan user, tanpa peran system."""
    prompt = quality_prompt(_INPUTS)

    assert prompt.startswith("Score the quality of the provider answer")
    assert "system" not in prompt.lower()
    assert _INPUTS.criteria in prompt
    assert _INPUTS.ground_truth in prompt
    assert _INPUTS.question in prompt
    assert _INPUTS.provider_answer in prompt
    assert _INPUTS.traps not in prompt
    assert "A chain of legal references does not all have to be named." in prompt
    assert format_prompt(_INPUTS).startswith("Score whether the provider answer fits")
    assert "Do not apply an outside format standard." in format_prompt(_INPUTS)
    assert "SCORE:" not in prompt


class _ScriptedJudge:
    """Mock penilai. Tidak didaftarkan di dispatch."""

    def __init__(self, replies: list[ProviderResponse]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def ask(
        self, prompt: str, *, parameters: object = None, timeout: float = 0
    ) -> ProviderResponse:
        del parameters, timeout
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _ok(text: str) -> ProviderResponse:
    return ProviderResponse(text=text, model_version="mock-judge", latency_ms=5, raw={})


def test_dua_prompt_per_pengulangan_membawa_empat_masukan():
    """Positif: kualitas lalu format, sekali per pengulangan, tanpa jebakan."""
    judge = _ScriptedJudge([_ok("nanti")] * 4)

    hasil = run_judge(judge, _INPUTS, repetitions=2)

    assert [baris.kind for baris in hasil.rows] == ["quality", "format", "quality", "format"]
    assert [baris.repetition for baris in hasil.rows] == [1, 1, 2, 2]
    assert judge.prompts[0] == quality_prompt(_INPUTS)
    assert judge.prompts[1] == format_prompt(_INPUTS)
    for prompt in judge.prompts:
        assert _INPUTS.traps not in prompt
