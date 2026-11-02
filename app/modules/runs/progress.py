"""Angka progres satu pengukuran (PBI-12 AC5, SCRUM-215).

Fungsi murni: tidak mengenal HTTP, database, atau jaringan. Dipakai
endpoint progres yang di-poll tiap beberapa detik, jadi semua hitungan
berasal dari angka yang sudah diagregasi, bukan dari query di sini.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.shared.exceptions import InternalError

_ONE_HUNDRED = Decimal(100)
_TENTH = Decimal("0.1")
_CENT = Decimal("0.01")
_ONE = Decimal(1)


@dataclass(frozen=True)
class ProgressNumbers:
    """Hasil hitungan progres, siap dipakai schema response."""

    completed_calls: int
    total_calls: int
    percent_complete: Decimal
    failed_calls: int
    percent_failed: Decimal
    remaining_seconds: int | None
    running_cost_idr: Decimal
    remaining_budget_idr: Decimal


def compute_progress(
    *,
    total_calls: int,
    completed_calls: int,
    failed_calls: int,
    started_at: datetime,
    now: datetime,
    cost_so_far_idr: Decimal,
    monthly_budget_idr: Decimal,
) -> ProgressNumbers:
    """Angka yang dibaca layar progres.

    Persen gagal dihitung dari panggilan yang sudah selesai, bukan dari
    total, supaya angkanya berarti sejak awal pengukuran. Perkiraan sisa
    waktu memakai kecepatan rata-rata sejauh ini dan dibulatkan ke atas
    supaya tidak menjanjikan lebih cepat dari kenyataan. Angka yang tidak
    masuk akal hanya bisa datang dari agregasi yang rusak, jadi hasilnya
    500, bukan 422.
    """
    _require_valid_inputs(
        total_calls=total_calls,
        completed_calls=completed_calls,
        failed_calls=failed_calls,
        started_at=started_at,
        now=now,
        cost_so_far_idr=cost_so_far_idr,
        monthly_budget_idr=monthly_budget_idr,
    )
    return ProgressNumbers(
        completed_calls=completed_calls,
        total_calls=total_calls,
        percent_complete=_percent(completed_calls, total_calls),
        failed_calls=failed_calls,
        percent_failed=_percent(failed_calls, completed_calls),
        remaining_seconds=_remaining_seconds(
            total_calls=total_calls,
            completed_calls=completed_calls,
            started_at=started_at,
            now=now,
        ),
        running_cost_idr=cost_so_far_idr.quantize(_CENT),
        remaining_budget_idr=(monthly_budget_idr - cost_so_far_idr).quantize(_CENT),
    )


def _percent(bagian: int, keseluruhan: int) -> Decimal:
    """Nol pembagi berarti belum ada apa-apa untuk dipersentasekan."""
    if keseluruhan == 0:
        return Decimal("0.0")
    hasil = Decimal(bagian) / Decimal(keseluruhan) * _ONE_HUNDRED
    return hasil.quantize(_TENTH, rounding=ROUND_HALF_UP)


def _remaining_seconds(
    *,
    total_calls: int,
    completed_calls: int,
    started_at: datetime,
    now: datetime,
) -> int | None:
    """None selama belum ada panggilan selesai, karena kecepatannya belum diketahui."""
    if completed_calls == 0:
        return None
    if completed_calls >= total_calls:
        return 0
    berjalan = Decimal(str((now - started_at).total_seconds()))
    per_panggilan = berjalan / Decimal(completed_calls)
    sisa = Decimal(total_calls - completed_calls)
    return int((per_panggilan * sisa).quantize(_ONE, rounding=ROUND_CEILING))


def _require_valid_inputs(
    *,
    total_calls: int,
    completed_calls: int,
    failed_calls: int,
    started_at: datetime,
    now: datetime,
    cost_so_far_idr: Decimal,
    monthly_budget_idr: Decimal,
) -> None:
    """Jumlah tidak boleh negatif, dan tidak boleh melebihi induknya."""
    salah = (
        total_calls < 0
        or completed_calls < 0
        or failed_calls < 0
        or completed_calls > total_calls
        or failed_calls > completed_calls
        or cost_so_far_idr < 0
        or monthly_budget_idr < 0
        or now < started_at
    )
    if salah:
        raise InternalError(
            "Progress inputs are out of range.",
            code="PROGRESS_INVALID_INPUT",
        )