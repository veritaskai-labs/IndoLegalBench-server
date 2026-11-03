"""Progress numbers for one measurement. SCRUM-77 SCRUM-215.

PBI-12 AC5: completed calls of the total, the percentage, the failures,
the estimated time left, and the running cost against the remaining
budget. The numbers never use float.
"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.runs import progress, schemas
from app.shared.exceptions import InternalError

_MULAI = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def _hitung(**ubah) -> progress.ProgressNumbers:
    nilai = {
        "total_calls": 300,
        "completed_calls": 240,
        "failed_calls": 12,
        "started_at": _MULAI,
        "now": _MULAI + timedelta(minutes=40),
        "running_cost_idr": Decimal("120000.00"),
        "spent_this_month_idr": Decimal("120000.00"),
        "monthly_budget_idr": Decimal("500000.00"),
    }
    nilai.update(ubah)
    return progress.compute_progress(**nilai)


def test_persen_selesai_dari_panggilan_yang_sudah_jadi():
    # Arrange: 240 dari 300

    # Act
    angka = _hitung()

    # Assert
    assert angka.completed_calls == 240
    assert angka.total_calls == 300
    assert angka.percent_complete == Decimal("80.0")


def test_persen_gagal_dihitung_dari_panggilan_yang_sudah_jadi():
    # Arrange: 12 gagal dari 240 yang sudah jadi

    # Act
    angka = _hitung()

    # Assert
    assert angka.failed_calls == 12
    assert angka.percent_failed == Decimal("5.0")


def test_sisa_waktu_mengikuti_kecepatan_sejauh_ini():
    # Arrange: 40 menit untuk 240 panggilan = 10 detik per panggilan,
    # sisa 60 panggilan = 600 detik

    # Act
    angka = _hitung()

    # Assert
    assert angka.remaining_seconds == 600


def test_sisa_waktu_kosong_sebelum_ada_panggilan_selesai():
    # Arrange + Act
    angka = _hitung(completed_calls=0, failed_calls=0)

    # Assert
    assert angka.remaining_seconds is None


def test_sisa_waktu_nol_saat_semua_panggilan_selesai():
    # Arrange + Act
    angka = _hitung(completed_calls=300)

    # Assert
    assert angka.remaining_seconds == 0


def test_sisa_anggaran_dari_biaya_berjalan():
    # Arrange + Act
    angka = _hitung()

    # Assert
    assert angka.running_cost_idr == Decimal("120000.00")
    assert angka.remaining_budget_idr == Decimal("380000.00")


def test_total_nol_tidak_membagi_nol():
    # Arrange: sesi yang belum punya panggilan sama sekali

    # Act
    angka = _hitung(total_calls=0, completed_calls=0, failed_calls=0)

    # Assert
    assert angka.percent_complete == Decimal("0.0")
    assert angka.percent_failed == Decimal("0.0")
    assert angka.remaining_seconds is None


def test_selesai_lebih_banyak_dari_total_ditolak():
    # Arrange + Act + Assert
    with pytest.raises(InternalError) as exc:
        _hitung(completed_calls=301)

    assert exc.value.code == "PROGRESS_INVALID_INPUT"


def test_gagal_lebih_banyak_dari_selesai_ditolak():
    # Arrange + Act + Assert
    with pytest.raises(InternalError) as exc:
        _hitung(failed_calls=241)

    assert exc.value.code == "PROGRESS_INVALID_INPUT"


def test_hasil_hitungan_langsung_jadi_response():
    # Arrange
    angka = _hitung()

    # Act
    respons = schemas.ProgressResponse(
        **asdict(angka),
        started_at=_MULAI,
        updated_at=_MULAI + timedelta(minutes=40),
    )

    # Assert
    assert respons.percent_complete == Decimal("80.0")
    assert respons.remaining_seconds == 600
    assert respons.remaining_budget_idr == Decimal("380000.00")


def test_sesi_yang_belum_mulai_tidak_menghitung_sisa_waktu():
    # Arrange: sesi masih antre, started_at di tabel belum terisi (SCRUM-205)

    # Act
    angka = _hitung(
        completed_calls=0,
        failed_calls=0,
        started_at=None,
        running_cost_idr=Decimal("0.00"),
    )

    # Assert
    assert angka.percent_complete == Decimal("0.0")
    assert angka.percent_failed == Decimal("0.0")
    assert angka.remaining_seconds is None
    assert angka.running_cost_idr == Decimal("0.00")
