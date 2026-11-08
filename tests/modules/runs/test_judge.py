"""Pesan user penilai. SCRUM-224."""

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

_PRICES_INPUT = Decimal("1")
_PRICES_OUTPUT = Decimal("2")
_PRICES_RATE = Decimal("16000")

_INPUTS = JudgeInputs(
    criteria="mentions Pasal 156\ncites UU 40/2007\nstates the conclusion\nnames the parties",
    ground_truth="Pasal 156 UU 40/2007",
    question="Apa akibat hukumnya?",
    provider_answer="Jawaban provider.",
    traps="jebakan rahasia yang tidak boleh masuk prompt",
)


def _kasus(*kriteria: str, traps: str | None = _INPUTS.traps) -> JudgeInputs:
    return JudgeInputs(
        criteria="\n".join(kriteria),
        ground_truth=_INPUTS.ground_truth,
        question=_INPUTS.question,
        provider_answer=_INPUTS.provider_answer,
        traps=traps,
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


def _ok(
    text: str, *, input_tokens: int | None = None, output_tokens: int | None = None
) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        model_version="mock-judge",
        latency_ms=5,
        raw={},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


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


def _reply(*pairs: tuple[str, bool], explanation: str = "Kriteria tidak terpenuhi.") -> str:
    blocks: list[str] = []
    for criterion, met in pairs:
        if met:
            blocks.append(f"CRITERION: {criterion}\nMET: yes")
        else:
            blocks.append(f"CRITERION: {criterion}\nMET: no\nEXPLANATION: {explanation}")
    return "\n".join(blocks)


def test_skor_proporsional_dan_alasan_placeholder():
    """Positif: 3 dari 4 menjadi 0.75, semua terpenuhi menjadi 1.00."""
    sebagian = _reply(("a", True), ("b", True), ("c", True), ("d", False))
    penuh = _reply(("a", True), ("b", True), ("c", True), ("d", True))
    judge = _ScriptedJudge([_ok(sebagian), _ok(penuh)])

    hasil = run_judge(judge, _kasus("a", "b", "c", "d"), repetitions=1)

    baris = hasil.rows[0]
    assert baris.score == Decimal("0.75")
    assert baris.criteria[3].met is False
    assert baris.criteria[3].explanation == "Kriteria tidak terpenuhi."
    assert baris.criteria[3].reason_id == "unmet"
    assert baris.reason_id == "unmet"
    assert hasil.rows[1].score == Decimal("1.00")
    assert hasil.rows[1].reason_id is None
    assert hasil.rows[1].explanation is None


def test_penjelasan_hanya_saat_kriteria_tidak_terpenuhi():
    """Edge: baris EXPLANATION pada MET yes tidak menjadi alasan."""
    teks = "CRITERION: menyebut pasal\nMET: yes\nEXPLANATION: tidak dipakai"
    judge = _ScriptedJudge([_ok(teks), _ok(_reply(("a", True)))])

    hasil = run_judge(judge, _kasus("menyebut pasal"), repetitions=1)

    assert hasil.rows[0].criteria[0].met is True
    assert hasil.rows[0].criteria[0].explanation is None
    assert hasil.rows[0].criteria[0].reason_id is None
    assert hasil.rows[0].score == Decimal("1.00")


def test_skor_dibulatkan_dua_desimal():
    """Edge: 2 dari 3 dibulatkan ke 0.67."""
    judge = _ScriptedJudge(
        [_ok(_reply(("a", True), ("b", True), ("c", False))), _ok(_reply(("a", True)))]
    )

    hasil = run_judge(judge, _kasus("a", "b", "c"), repetitions=1)

    assert hasil.rows[0].score == Decimal("0.67")


def test_jebakan_tidak_mengubah_prompt_maupun_skor():
    """Negatif: isi jebakan tidak masuk pesan dan tidak mengubah skor."""
    tanpa = _kasus("a", "b", traps=None)

    def _jawaban() -> list[ProviderResponse]:
        return [
            _ok(_reply(("a", True), ("b", False))),
            _ok(_reply(("a", True), ("b", False))),
        ]

    pertama = run_judge(_ScriptedJudge(_jawaban()), _kasus("a", "b"), repetitions=1)
    kedua = run_judge(_ScriptedJudge(_jawaban()), tanpa, repetitions=1)

    assert pertama.rows[0].prompt == kedua.rows[0].prompt
    assert pertama.rows[0].score == kedua.rows[0].score == Decimal("0.50")
    assert "jebakan rahasia" not in pertama.rows[0].prompt


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


def test_kriteria_kasus_adalah_penyebut_dan_balasan_parsial_tidak_sah():
    """Negatif: hilang, ganda, atau asing tidak berskor. Penyebut bukan jumlah blok."""
    kasus = _kasus("a", "b", "c", "d")

    def nilai(teks: str):
        judge = _ScriptedJudge([_ok(teks), _ok("bukan penilaian")])
        return run_judge(judge, kasus, repetitions=1).rows[0]

    hilang = nilai(_reply(("a", True), ("b", True)))
    assert hilang.score is None
    assert hilang.error == "invalid_judge_reply"
    assert hilang.failure_kind == "invalid_response"
    assert hilang.criteria == ()

    ganda = nilai(_reply(("a", True), ("a", True), ("b", False), ("c", True), ("d", False)))
    assert ganda.score is None
    assert ganda.error == "invalid_judge_reply"

    asing = nilai(_reply(("a", True), ("b", False), ("c", True), ("z", False)))
    assert asing.score is None
    assert asing.error == "invalid_judge_reply"


def test_met_dengan_tanda_baca_dan_bintang_tetap_dihitung():
    """Edge: MET Yes. dan **MET:** tidak dibuang, lalu dihitung pada kriteria kasus."""
    teks = "CRITERION: a\nMET: Yes.\nCRITERION: b\n**MET:** no\nEXPLANATION: tidak lengkap"
    judge = _ScriptedJudge([_ok(teks), _ok("bukan penilaian")])

    baris = run_judge(judge, _kasus("a", "b"), repetitions=1).rows[0]

    assert baris.error is None
    assert baris.score == Decimal("0.50")
    assert baris.criteria[0].criterion == "a"
    assert baris.criteria[0].met is True
    assert baris.criteria[1].met is False
    assert baris.criteria[1].explanation == "tidak lengkap"


def test_balasan_tanpa_kriteria_tidak_sah():
    """Edge: sukses tanpa blok CRITERION bukan skor."""
    judge = _ScriptedJudge([_ok("tidak ada blok"), _ok(_reply(("a", True)))])

    hasil = run_judge(judge, _INPUTS, repetitions=1)

    assert hasil.rows[0].error == "invalid_judge_reply"
    assert hasil.rows[0].score is None
    assert hasil.rows[0].failure_kind == "invalid_response"


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


def test_biaya_baris_mengikuti_actual_cost_idr():
    """Positif: token kali harga kali kurs. Token kosong membuat biaya kosong."""
    prices = JudgePrices(
        usd_per_million_input_tokens=_PRICES_INPUT,
        usd_per_million_output_tokens=_PRICES_OUTPUT,
        usd_idr_rate=_PRICES_RATE,
    )
    berbiaya = _ok(_reply(("a", True)), input_tokens=1_000_000, output_tokens=0)
    kosong = _ok(_reply(("a", True)), input_tokens=None, output_tokens=None)
    judge = _ScriptedJudge([berbiaya, kosong])

    hasil = run_judge(judge, _INPUTS, repetitions=1, prices=prices)

    diharapkan = actual_cost_idr(
        input_tokens=1_000_000,
        output_tokens=0,
        usd_per_million_input_tokens=prices.usd_per_million_input_tokens,
        usd_per_million_output_tokens=prices.usd_per_million_output_tokens,
        usd_idr_rate=prices.usd_idr_rate,
    )
    assert hasil.rows[0].cost_idr == diharapkan
    assert hasil.rows[1].cost_idr is None


def test_baris_membawa_token_dan_kurs_untuk_dicatat():
    """Positif: token dan kurs tiap panggilan ikut di baris supaya biaya bisa disimpan."""
    # Arrange
    prices = JudgePrices(
        usd_per_million_input_tokens=_PRICES_INPUT,
        usd_per_million_output_tokens=_PRICES_OUTPUT,
        usd_idr_rate=_PRICES_RATE,
    )
    judge = _ScriptedJudge(
        [
            _ok(_reply(("a", True)), input_tokens=120, output_tokens=30),
            _ok(_reply(("a", True)), input_tokens=80, output_tokens=10),
        ]
    )

    # Act
    hasil = run_judge(judge, _INPUTS, repetitions=1, prices=prices)

    # Assert
    kualitas, format_ = hasil.rows
    assert (kualitas.input_tokens, kualitas.output_tokens) == (120, 30)
    assert (format_.input_tokens, format_.output_tokens) == (80, 10)
    assert kualitas.usd_idr_rate == _PRICES_RATE
    assert format_.usd_idr_rate == _PRICES_RATE


def test_baris_tanpa_harga_tidak_membawa_kurs_tetapi_token_tetap_ada():
    """Edge: tanpa JudgePrices kurs kosong, token tetap dilaporkan apa adanya."""
    # Arrange
    judge = _ScriptedJudge(
        [
            _ok(_reply(("a", True)), input_tokens=5, output_tokens=7),
            _ok(_reply(("a", True)), input_tokens=None, output_tokens=None),
        ]
    )

    # Act
    hasil = run_judge(judge, _INPUTS, repetitions=1)

    # Assert
    assert hasil.rows[0].usd_idr_rate is None
    assert (hasil.rows[0].input_tokens, hasil.rows[0].output_tokens) == (5, 7)
    assert (hasil.rows[1].input_tokens, hasil.rows[1].output_tokens) == (None, None)


def test_panggilan_gagal_tetap_membawa_token_yang_terpakai():
    """Edge: panggilan gagal yang sudah memakai token tetap dilaporkan untuk dicatat."""
    # Arrange
    prices = JudgePrices(
        usd_per_million_input_tokens=_PRICES_INPUT,
        usd_per_million_output_tokens=_PRICES_OUTPUT,
        usd_idr_rate=_PRICES_RATE,
    )
    gagal = ProviderResponse(
        text="",
        model_version="",
        latency_ms=1,
        raw={},
        error="upstream_error",
        failure_kind="provider_error",
        input_tokens=40,
        output_tokens=0,
    )
    judge = _ScriptedJudge([gagal, _ok(_reply(("a", True)))])

    # Act
    hasil = run_judge(judge, _INPUTS, repetitions=1, prices=prices)

    # Assert
    assert hasil.rows[0].score is None
    assert hasil.rows[0].input_tokens == 40
    assert hasil.rows[0].cost_idr is not None
