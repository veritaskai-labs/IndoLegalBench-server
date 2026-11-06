"""Aturan antar-field untuk versi rubrik baru (PBI-14 AC3, AC7, SCRUM-222 PR B).

Fungsi murni tanpa database. Service memanggilnya sebelum menyimpan, sehingga isi yang
salah menjadi 422 dengan code dan path field yang jelas. Trigger PostgreSQL migrasi
ac845acfa98d memeriksa aturan yang sama sebagai pengaman terakhir.

Masalah dilaporkan satu per satu, dari yang paling atas di form: data penyetuju, lalu
aspek, lalu level. Nama dibandingkan tanpa membedakan huruf besar dan kecil.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.modules.grading.schemas import RubricVersionCreate

APPROVED_AT_WITHOUT_NAME = "RUBRIC_APPROVED_AT_WITHOUT_NAME"
DUPLICATE_ASPECT_NAME = "RUBRIC_DUPLICATE_ASPECT_NAME"
WEIGHTS_INCOMPLETE = "RUBRIC_WEIGHTS_INCOMPLETE"
WEIGHTS_NOT_100 = "RUBRIC_WEIGHTS_NOT_100"
DUPLICATE_LEVEL_SCORE = "RUBRIC_DUPLICATE_LEVEL_SCORE"
UNKNOWN_DESCRIPTOR_ASPECT = "RUBRIC_UNKNOWN_DESCRIPTOR_ASPECT"
DUPLICATE_DESCRIPTOR = "RUBRIC_DUPLICATE_DESCRIPTOR"

_TOTAL_BOBOT = Decimal(100)


@dataclass(frozen=True)
class RubricProblem:
    code: str
    field: str
    message: str


def rubric_problem(version: RubricVersionCreate) -> RubricProblem | None:
    """Masalah pertama pada isi versi, atau None kalau isinya sah."""
    checks: tuple[Callable[[RubricVersionCreate], RubricProblem | None], ...] = (
        _approval,
        _aspect_names,
        _weights,
        _level_scores,
        _descriptors,
    )
    for check in checks:
        masalah = check(version)
        if masalah is not None:
            return masalah
    return None


def _level_field(nomor_level: int, sisa: str) -> str:
    """Path field di dalam satu level, misalnya levels.0.descriptors.1.aspect_name."""
    return f"levels.{nomor_level}.{sisa}"


def _kunci(nama: str) -> str:
    return nama.casefold()


def _ganda_pertama(nilai: Iterable) -> int | None:
    """Posisi kemunculan kedua dari nilai yang sudah pernah muncul, atau None."""
    terlihat = set()
    for posisi, isi in enumerate(nilai):
        if isi in terlihat:
            return posisi
        terlihat.add(isi)
    return None


def _approval(version: RubricVersionCreate) -> RubricProblem | None:
    if version.approved_at is not None and version.approved_by_name is None:
        return RubricProblem(
            APPROVED_AT_WITHOUT_NAME,
            "approved_at",
            "Tanggal persetujuan hanya bisa diisi bersama nama penyetuju.",
        )
    return None


def _aspect_names(version: RubricVersionCreate) -> RubricProblem | None:
    posisi = _ganda_pertama(_kunci(aspek.name) for aspek in version.aspects)
    if posisi is None:
        return None
    return RubricProblem(
        DUPLICATE_ASPECT_NAME,
        f"aspects.{posisi}.name",
        "Nama dimensi sudah dipakai di versi ini.",
    )


def _weights(version: RubricVersionCreate) -> RubricProblem | None:
    bobot = [aspek.weight for aspek in version.aspects]
    terisi = [nilai for nilai in bobot if nilai is not None]
    if not terisi:
        return None
    if len(terisi) < len(bobot):
        return RubricProblem(
            WEIGHTS_INCOMPLETE,
            "aspects",
            "Isi bobot untuk semua dimensi, atau kosongkan semuanya.",
        )
    if sum(terisi) != _TOTAL_BOBOT:
        return RubricProblem(WEIGHTS_NOT_100, "aspects", "Total bobot semua dimensi harus 100.")
    return None


def _level_scores(version: RubricVersionCreate) -> RubricProblem | None:
    posisi = _ganda_pertama(level.score for level in version.levels)
    if posisi is None:
        return None
    return RubricProblem(
        DUPLICATE_LEVEL_SCORE,
        _level_field(posisi, "score"),
        "Skor level sudah dipakai di versi ini.",
    )


def _descriptors(version: RubricVersionCreate) -> RubricProblem | None:
    dikenal = {_kunci(aspek.name) for aspek in version.aspects}
    for nomor_level, level in enumerate(version.levels):
        nama = [_kunci(deskriptor.aspect_name) for deskriptor in level.descriptors]
        for nomor, kunci in enumerate(nama):
            if kunci not in dikenal:
                return RubricProblem(
                    UNKNOWN_DESCRIPTOR_ASPECT,
                    _level_field(nomor_level, f"descriptors.{nomor}.aspect_name"),
                    "Deskriptor merujuk dimensi yang tidak ada di versi ini.",
                )
        ganda = _ganda_pertama(nama)
        if ganda is not None:
            return RubricProblem(
                DUPLICATE_DESCRIPTOR,
                _level_field(nomor_level, f"descriptors.{ganda}.aspect_name"),
                "Dimensi ini sudah punya deskriptor di level yang sama.",
            )
    return None
