"""Persen kasus selesai satu sesi (PBI-12 AC5, SCRUM-265).

Fungsi murni: dipakai endpoint progres dan daftar sesi, jadi keduanya pasti
menampilkan angka yang sama. Hitungan kasusnya dari repository.case_completion.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseProgress:
    """Angka "X dari N kasus" dan persennya, siap dipakai schema response."""

    completed_cases: int
    total_cases: int
    percent_cases_complete: int


def case_progress(*, completed_cases: int, total_cases: int) -> CaseProgress:
    """Persen bulat ke bawah, dibatasi 0 sampai 100.

    Ke bawah supaya 100 hanya muncul bila semua kasus memang selesai
    (199 dari 200 tetap 99). Total nol berarti sesi belum punya kasus, jadi 0.
    Batas 0-100 menjaga bar progres bila agregasinya ternyata rusak.
    """
    persen = 0 if total_cases <= 0 else completed_cases * 100 // total_cases
    return CaseProgress(
        completed_cases=completed_cases,
        total_cases=total_cases,
        percent_cases_complete=max(0, min(100, persen)),
    )
