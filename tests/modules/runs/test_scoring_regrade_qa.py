"""QA nilai ulang dan riwayat hasil penilaian. PBI-14 (SCRUM-78) AC7; SCRUM-229.

Membuktikan nilai ulang menambah hasil terbaru tanpa menghapus yang lama, hasil lama
tetap memakai rubriknya sendiri, dan hanya jalur LLM judge yang bergantung pada versi
rubrik. Jalur deterministik tidak pernah perlu dinilai ulang karena rumusnya tidak
mengandung rubrik.

Penyimpanan SQL-nya ada di app/modules/grading/regrade_sql.py dan diuji di tes
integrasi PostgreSQL; di sini yang diuji aturannya, tanpa database.

Label: Positif = perilaku yang diharapkan, Negatif = harus ditolak, Edge = batas perilaku.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.modules.runs.score_aggregation import (
    GradePath,
    GradingResult,
    RubricVersion,
    latest_results,
    mark_latest,
    needs_regrade,
)

V1 = RubricVersion(version="v1")
V2 = RubricVersion(version="v2")
PERCOBAAN = uuid.UUID("11111111-1111-1111-1111-111111111111")
LAIN = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _hasil(
    attempt_id: uuid.UUID,
    path: GradePath,
    *,
    skor: str,
    jam: int,
    rubrik: str | None = None,
) -> GradingResult:
    return GradingResult(
        attempt_id=attempt_id,
        path=path,
        score=Decimal(skor),
        graded_at=datetime(2026, 11, 8, jam, 0, tzinfo=UTC),
        rubric_version=rubrik,
    )


# Hasil lama tetap tersimpan


def test_nilai_ulang_menambah_hasil_dan_yang_lama_tetap_ada():
    """Positif: riwayat bertambah, tidak ditimpa (AC7)."""
    lama = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")
    baru = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.90", jam=15, rubrik="v2")

    riwayat = mark_latest([lama, baru])

    assert len(riwayat) == 2
    assert [catatan.is_latest for catatan in riwayat] == [False, True]


def test_hasil_lama_tetap_memakai_rubriknya_sendiri():
    """Positif: versi rubrik pada hasil lama tidak ikut diperbarui (AC7)."""
    lama = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")
    baru = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.90", jam=15, rubrik="v2")

    riwayat = mark_latest([lama, baru])

    usang = next(catatan.result for catatan in riwayat if not catatan.is_latest)
    assert usang.rubric_version == "v1"
    assert usang.score == Decimal("0.50")
    assert usang.graded_at == datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def test_terbaru_dihitung_per_percobaan_dan_per_jalur():
    """Edge: menilai ulang judge tidak mengganggu skor deterministik (AC1, AC7)."""
    deterministik = _hasil(PERCOBAAN, GradePath.DETERMINISTIC, skor="1.00", jam=9)
    judge_lama = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")
    judge_baru = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.90", jam=15, rubrik="v2")

    terbaru = latest_results([deterministik, judge_lama, judge_baru])

    assert len(terbaru) == 2
    per_jalur = {hasil.path: hasil for hasil in terbaru}
    assert per_jalur[GradePath.DETERMINISTIC].score == Decimal("1.00")
    assert per_jalur[GradePath.JUDGE].score == Decimal("0.90")


def test_percobaan_lain_tidak_tersentuh():
    """Edge: riwayat satu percobaan tidak memengaruhi percobaan lain (AC7)."""
    satu = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")
    dua = _hasil(LAIN, GradePath.JUDGE, skor="0.70", jam=9, rubrik="v1")

    terbaru = latest_results([satu, dua])

    assert {hasil.attempt_id for hasil in terbaru} == {PERCOBAAN, LAIN}


# Siapa yang perlu dinilai ulang


def test_judge_dengan_rubrik_lama_perlu_dinilai_ulang():
    """Positif: hasil judge yang rubriknya ketinggalan masuk daftar (AC7)."""
    hasil = [_hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")]

    assert needs_regrade(hasil, V2) == (PERCOBAAN,)


def test_judge_yang_sudah_rubrik_terbaru_tidak_dinilai_ulang():
    """Negatif: idempoten, panggilan kedua tidak menambah pekerjaan (AC7)."""
    hasil = [_hasil(PERCOBAAN, GradePath.JUDGE, skor="0.90", jam=15, rubrik="v2")]

    assert needs_regrade(hasil, V2) == ()


def test_jalur_deterministik_tidak_pernah_perlu_dinilai_ulang():
    """Edge: rumus deterministik tidak mengandung rubrik, jadi versinya tidak relevan (AC1)."""
    hasil = [_hasil(PERCOBAAN, GradePath.DETERMINISTIC, skor="1.00", jam=9, rubrik="v1")]

    assert needs_regrade(hasil, V2) == ()


def test_hanya_hasil_terbaru_yang_menentukan_perlu_nilai_ulang():
    """Edge: hasil lama berubah rubrik tidak membuat percobaan dinilai ulang dua kali (AC7)."""
    lama = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")
    baru = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.90", jam=15, rubrik="v2")

    assert needs_regrade([lama, baru], V2) == ()


def test_hasil_tanpa_versi_rubrik_dianggap_ketinggalan():
    """Edge: baris lama sebelum versi rubrik dicatat tetap ikut dinilai ulang (AC7)."""
    hasil = [_hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik=None)]

    assert needs_regrade(hasil, V1) == (PERCOBAAN,)


def test_waktu_sama_dimenangkan_masukan_terakhir():
    """Edge: perilaku yang didokumentasikan saat graded_at seri, dikunci di sini (AC7)."""
    pertama = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.50", jam=9, rubrik="v1")
    kedua = _hasil(PERCOBAAN, GradePath.JUDGE, skor="0.90", jam=9, rubrik="v2")

    (terbaru,) = latest_results([pertama, kedua])

    assert terbaru.score == Decimal("0.90")
