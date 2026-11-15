"""Kode alasan sebuah grading run gagal. SCRUM-78 SCRUM-289 (K-19).

Kode judge_* sudah dipakai session_grade untuk tiap percobaan; di sini semuanya dipakai ulang
dan disatukan menjadi satu kode untuk run. Hanya kode dari daftar ini yang pernah disimpan:
teks galat provider bisa memuat kredensial atau alamat endpoint, jadi tidak ikut tersimpan.
"""

from collections.abc import Iterable

from app.modules.runs.session_grade import (
    JUDGE_BUDGET_EXHAUSTED,
    JUDGE_INCOMPLETE,
    JUDGE_NOT_CONFIGURED,
    JUDGE_UNAVAILABLE,
)

NO_SCORE = "NO_SCORE"
GRADING_ERROR = "GRADING_ERROR"
JUDGE_PRICING_MISSING = "judge_pricing_missing"

# Yang harus Admin perbaiki lebih dulu ada di depan: konfigurasi judge, lalu anggaran, lalu
# jawaban judge yang tidak lengkap. Menambah alasan baru cukup menambah satu baris di sini.
_PRIORITY = (
    JUDGE_NOT_CONFIGURED,
    JUDGE_UNAVAILABLE,
    JUDGE_BUDGET_EXHAUSTED,
    JUDGE_INCOMPLETE,
)


def resolve_failure_code(
    judge_errors: Iterable[str | None], *, pricing_missing: bool = False
) -> str:
    """Satu kode untuk run yang gagal, dari galat judge tiap percobaannya.

    Harga judge yang kosong menang atas semuanya: tidak ada percobaan yang sempat dinilai.
    Tanpa galat judge sama sekali (jawaban kosong atau ditolak) run gagal karena tidak ada skor.
    """
    if pricing_missing:
        return JUDGE_PRICING_MISSING
    errors = {error for error in judge_errors if error}
    if not errors:
        return NO_SCORE
    for code in _PRIORITY:
        if code in errors:
            return code
    return GRADING_ERROR
