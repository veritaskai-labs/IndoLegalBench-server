"""Pesan user penilai. SCRUM-224."""

from app.modules.runs.judge import JudgeInputs, format_prompt, quality_prompt

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
