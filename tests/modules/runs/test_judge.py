"""Penilai LLM: prompt, skor, jebakan, biaya, dan balasan gagal. SCRUM-224."""

from decimal import Decimal

from app.modules.providers.adapters.base import ProviderResponse
from app.modules.runs.call_cost import actual_cost_idr
from app.modules.runs.judge import (
    JudgeInputs,
    JudgePrices,
    format_prompt,
    quality_prompt,
    run_judge,
)

_PRICES = JudgePrices(
    usd_per_million_input_tokens=Decimal("1"),
    usd_per_million_output_tokens=Decimal("2"),
    usd_idr_rate=Decimal("16000"),
)
_INPUTS = JudgeInputs(
    criteria="mentions Pasal 156\ncites UU 40/2007\nstates the conclusion\nnames the parties",
    ground_truth="Pasal 156 UU 40/2007",
    question="Apa akibat hukumnya?",
    provider_answer="Jawaban provider.",
    traps="jebakan rahasia yang tidak boleh masuk prompt",
)


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


def _ok(
    text: str, *, input_tokens: int | None = 1000, output_tokens: int | None = 500
) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        model_version="mock-judge",
        latency_ms=5,
        raw={},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _reply(*pairs: tuple[str, bool], explanation: str = "Kriteria tidak terpenuhi.") -> str:
    blocks: list[str] = []
    for criterion, met in pairs:
        if met:
            blocks.append(f"CRITERION: {criterion}\nMET: yes")
        else:
            blocks.append(f"CRITERION: {criterion}\nMET: no\nEXPLANATION: {explanation}")
    return "\n".join(blocks)


def test_dua_prompt_per_pengulangan_membawa_empat_masukan():
    """Positif: kualitas lalu format, sekali per pengulangan, tanpa jebakan."""
    judge = _ScriptedJudge([_ok(_reply(("a", True)))] * 4)

    hasil = run_judge(judge, _INPUTS, repetitions=2, prices=_PRICES)

    assert [baris.kind for baris in hasil.rows] == ["quality", "format", "quality", "format"]
    assert [baris.repetition for baris in hasil.rows] == [1, 1, 2, 2]
    assert judge.prompts[0] == quality_prompt(_INPUTS)
    assert judge.prompts[1] == format_prompt(_INPUTS)
    for prompt in judge.prompts:
        assert _INPUTS.criteria in prompt
        assert _INPUTS.ground_truth in prompt
        assert _INPUTS.question in prompt
        assert _INPUTS.provider_answer in prompt
        assert _INPUTS.traps not in prompt
    assert "A chain of legal references does not all have to be named." in judge.prompts[0]
    assert "Do not apply an outside format standard." in judge.prompts[1]


def test_skor_proporsional_dan_alasan_placeholder():
    """Positif: 3 dari 4 menjadi 0.75, semua terpenuhi menjadi 1.00."""
    sebagian = _reply(("a", True), ("b", True), ("c", True), ("d", False))
    penuh = _reply(("a", True), ("b", True))
    judge = _ScriptedJudge([_ok(sebagian), _ok(penuh)])

    hasil = run_judge(judge, _INPUTS, repetitions=1, prices=_PRICES)

    baris = hasil.rows[0]
    assert baris.score == Decimal("0.75")
    assert baris.criteria[3].met is False
    assert baris.criteria[3].explanation == "Kriteria tidak terpenuhi."
    assert baris.criteria[3].reason_id == "unmet"
    assert baris.reason_id == "unmet"
    assert hasil.rows[1].score == Decimal("1.00")
    assert hasil.rows[1].reason_id is None
    assert hasil.rows[1].explanation is None


def test_panggilan_gagal_tanpa_skor():
    """Negatif: error adapter dikembalikan dan skor kosong."""
    gagal = ProviderResponse(
        text="CRITERION: a\nMET: yes",
        model_version="",
        latency_ms=5,
        raw={},
        error="upstream_error",
        failure_kind="provider_error",
    )
    judge = _ScriptedJudge([gagal, _ok(_reply(("a", True)))])

    hasil = run_judge(judge, _INPUTS, repetitions=1)

    assert hasil.rows[0].error == "upstream_error"
    assert hasil.rows[0].failure_kind == "provider_error"
    assert hasil.rows[0].score is None
    assert hasil.rows[0].criteria == ()


def test_balasan_tanpa_kriteria_tidak_sah():
    """Edge: sukses tanpa blok CRITERION bukan skor, dan bukan catatan gangguan."""
    judge = _ScriptedJudge([_ok("tidak ada blok"), _ok(_reply(("a", True)))])

    hasil = run_judge(judge, _INPUTS, repetitions=1)

    assert hasil.rows[0].error == "invalid_judge_reply"
    assert hasil.rows[0].score is None
    assert hasil.rows[0].failure_kind == "invalid_response"


def test_biaya_baris_mengikuti_actual_cost_idr():
    """Positif: token kali harga kali kurs. Token kosong membuat biaya kosong."""
    berbiaya = _ok(_reply(("a", True)), input_tokens=1_000_000, output_tokens=0)
    kosong = _ok(_reply(("a", True)), input_tokens=None, output_tokens=None)
    judge = _ScriptedJudge([berbiaya, kosong])

    hasil = run_judge(judge, _INPUTS, repetitions=1, prices=_PRICES)

    diharapkan = actual_cost_idr(
        input_tokens=1_000_000,
        output_tokens=0,
        usd_per_million_input_tokens=_PRICES.usd_per_million_input_tokens,
        usd_per_million_output_tokens=_PRICES.usd_per_million_output_tokens,
        usd_idr_rate=_PRICES.usd_idr_rate,
    )
    assert hasil.rows[0].cost_idr == diharapkan
    assert hasil.rows[1].cost_idr is None


def test_penjelasan_hanya_saat_kriteria_tidak_terpenuhi():
    """Edge: baris EXPLANATION pada MET yes tidak menjadi alasan."""
    teks = "CRITERION: menyebut pasal\nMET: yes\nEXPLANATION: tidak dipakai"
    judge = _ScriptedJudge([_ok(teks), _ok(_reply(("a", True)))])

    hasil = run_judge(judge, _INPUTS, repetitions=1)

    assert hasil.rows[0].criteria[0].met is True
    assert hasil.rows[0].criteria[0].explanation is None
    assert hasil.rows[0].criteria[0].reason_id is None
    assert hasil.rows[0].score == Decimal("1.00")


def test_skor_dibulatkan_dua_desimal():
    """Edge: 2 dari 3 dibulatkan ke 0.67, bukan persen dan bukan pecahan teks."""
    judge = _ScriptedJudge(
        [_ok(_reply(("a", True), ("b", True), ("c", False))), _ok(_reply(("a", True)))]
    )

    hasil = run_judge(judge, _INPUTS, repetitions=1)

    assert hasil.rows[0].score == Decimal("0.67")
    assert "SCORE:" not in hasil.rows[0].prompt


def test_jebakan_tidak_mengubah_prompt_maupun_skor():
    """Negatif: isi jebakan tidak masuk pesan dan tidak mengubah skor."""
    dengan = _INPUTS
    tanpa = JudgeInputs(
        criteria=_INPUTS.criteria,
        ground_truth=_INPUTS.ground_truth,
        question=_INPUTS.question,
        provider_answer=_INPUTS.provider_answer,
    )

    def _jawaban() -> list[ProviderResponse]:
        return [
            _ok(_reply(("a", True), ("b", False))),
            _ok(_reply(("a", True), ("b", False))),
        ]

    pertama = run_judge(_ScriptedJudge(_jawaban()), dengan, repetitions=1)
    kedua = run_judge(_ScriptedJudge(_jawaban()), tanpa, repetitions=1)

    assert pertama.rows[0].prompt == kedua.rows[0].prompt
    assert pertama.rows[1].prompt == kedua.rows[1].prompt
    assert pertama.rows[0].score == kedua.rows[0].score == Decimal("0.50")
    assert "jebakan rahasia" not in pertama.rows[0].prompt


def test_pesan_user_tanpa_system_prompt():
    """Negatif: instruksi tinggal di pesan user, tanpa peran system."""
    prompt = quality_prompt(_INPUTS)

    assert prompt.startswith("Score the quality of the provider answer")
    assert "system" not in prompt.lower()
    assert format_prompt(_INPUTS).startswith("Score whether the provider answer fits")


def test_failure_kind_tanpa_pesan_error_tetap_tanpa_skor():
    """Negatif: jenis gagal adapter cukup untuk mengosongkan skor."""
    gagal = ProviderResponse(
        text="CRITERION: a\nMET: yes",
        model_version="",
        latency_ms=1,
        raw={},
        failure_kind="timeout",
    )
    judge = _ScriptedJudge([gagal, _ok(_reply(("a", True)))])

    hasil = run_judge(judge, _INPUTS, repetitions=1)

    assert hasil.rows[0].score is None
    assert hasil.rows[0].failure_kind == "timeout"
    assert hasil.rows[0].criteria == ()
