"""Agregasi skor percobaan, kasus, dan produk. PBI-14 AC3, AC7, PBI-11 AC9 (dipindah). SCRUM-225.

Setiap angka di sini berpasangan deterministik dan judge lewat ScorePair, dan masing-masing tetap di skalanya sendiri:
jalur deterministik 0 sampai 1, jalur judge pada skala rubriknya.

Skor keluar sebagai Decimal dua digit di belakang koma, bukan persen dan bukan bentuk
"X dari N" (AC3). Pembulatannya ROUND_HALF_UP, sama dengan citation_score dan judge.

"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from types import MappingProxyType

from app.modules.cases.models import SplitTag

_DUA_DIGIT = Decimal("0.01")

# Kasus tanpa kategori tetap dihitung, tidak dibuang (PBI-14 AC10).
LAIN_LAIN = "Lain-lain"


class RubricAspect(StrEnum):
    """Tiga aspek rubrik kualitas jawaban hukum AI, rubrik v1 27 Oktober 2026."""

    KESIMPULAN = "kesimpulan"
    DASAR_HUKUM = "dasar_hukum"
    ARGUMENTASI = "argumentasi"


@dataclass(frozen=True)
class RubricVersion:
    """Satu versi rubrik: aspeknya, bobotnya, dan skor tertinggi per aspek.

    Versinya ikut tersimpan bersama hasil penilaian, supaya hasil lama tetap terbaca
    dengan rubrik yang dipakai saat itu (AC7).
    """

    version: str
    max_aspect_score: Decimal
    weights: Mapping[RubricAspect, Decimal]


RUBRIC_V1 = RubricVersion(
    version="v1",
    max_aspect_score=Decimal(5),
    weights=MappingProxyType(
        {
            RubricAspect.KESIMPULAN: Decimal(1),
            RubricAspect.DASAR_HUKUM: Decimal(1),
            RubricAspect.ARGUMENTASI: Decimal(1),
        }
    ),
)


@dataclass(frozen=True)
class ScorePair:

    deterministic: Decimal | None
    judge: Decimal | None


@dataclass(frozen=True)
class CaseScore:
    """Satu kasus dengan angka akhirnya, setelah pengulangan dirata-ratakan.

    split_tag dan category diambil dari snapshot sesi, bukan dari kasus yang sekarang,
    supaya angka lama tidak berubah ketika kasusnya diedit (D3).
    """

    case_code: str
    split_tag: SplitTag
    category: str | None
    scores: ScorePair


@dataclass(frozen=True)
class ThreeScores:
    """Tiga skor terpisah yang diminta PBI-11 AC9: kasus dev, kasus test, dan gabungan."""

    dev: ScorePair
    test: ScorePair
    combined: ScorePair


@dataclass(frozen=True)
class CategoryScore:
    """Pecahan per kategori. case_count ikut supaya pembaca tahu angka itu dari berapa kasus."""

    category: str
    case_count: int
    scores: ScorePair


@dataclass(frozen=True)
class GradingResult:
    """Satu hasil penilaian satu percobaan, dengan rubrik yang dipakai saat itu."""

    attempt_id: uuid.UUID
    rubric_version: str
    graded_at: datetime
    scores: ScorePair


@dataclass(frozen=True)
class GradingRecord:
    """Hasil penilaian beserta penanda apakah ia yang terbaru untuk percobaannya."""

    result: GradingResult
    is_latest: bool


def attempt_score(
    aspects: Mapping[RubricAspect, Decimal | int],
    rubric: RubricVersion = RUBRIC_V1,
) -> Decimal:
    """Skor satu percobaan: rata-rata berbobot aspek rubrik, pada skala rubriknya.

    Angka tetap di skala 0 sampai max_aspect_score, tidak diubah jadi persen (AC3).
    Aspek yang kurang atau nilai di luar skala ditolak, karena skor separuh terisi
    lebih berbahaya daripada kesalahan yang terlihat.
    """
    kurang = sorted(aspek.value for aspek in rubric.weights if aspek not in aspects)
    if kurang:
        raise ValueError(f"aspek rubrik belum dinilai: {', '.join(kurang)}")

    total_bobot = sum(rubric.weights.values(), Decimal(0))
    if total_bobot <= 0:
        raise ValueError("total bobot rubrik harus lebih dari nol")

    jumlah = Decimal(0)
    for aspek, bobot in rubric.weights.items():
        nilai = Decimal(aspects[aspek])
        if nilai < 0 or nilai > rubric.max_aspect_score:
            raise ValueError(
                f"nilai aspek {aspek.value} di luar skala 0 sampai {rubric.max_aspect_score}"
            )
        jumlah += nilai * bobot
    return _dua_digit(jumlah / total_bobot)


def criteria_score(met: int, total: int) -> Decimal | None:
    """Skor kasus yang proporsional terhadap kriteria, misalnya 3 dari 4 jadi 0.75.

    Kasus tanpa kriteria tidak bisa dinilai, jadi hasilnya None dan kasus itu tidak
    ikut menarik rata-rata ke bawah.
    """
    if met < 0 or total < 0:
        raise ValueError("jumlah kriteria tidak boleh negatif")
    if met > total:
        raise ValueError("kriteria terpenuhi tidak boleh melebihi jumlah kriteria")
    if total == 0:
        return None
    return _dua_digit(Decimal(met) / Decimal(total))


def mean_score(scores: Iterable[Decimal | None]) -> Decimal | None:
    """Rata-rata sederhana, bobot sama, mengabaikan yang tidak punya angka.

    None keluar hanya bila tidak ada satu pun angka, bukan saat sebagian kosong.
    """
    nilai = [skor for skor in scores if skor is not None]
    if not nilai:
        return None
    return _dua_digit(sum(nilai, Decimal(0)) / Decimal(len(nilai)))


def repetition_score(attempt_scores: Iterable[Decimal | None]) -> Decimal | None:
    """Angka satu kasus dari beberapa pengulangan: rata-rata skor percobaan.
    """
    return mean_score(attempt_scores)


def pair_mean(cases: Sequence[CaseScore]) -> ScorePair:
    """Rata-rata kedua jalur secara terpisah, bobot kasus sama rata."""
    return ScorePair(
        deterministic=mean_score(kasus.scores.deterministic for kasus in cases),
        judge=mean_score(kasus.scores.judge for kasus in cases),
    )


def product_score(cases: Sequence[CaseScore]) -> ScorePair:
    """Skor satu produk: rata-rata sederhana semua kasus dengan bobot sama (AC3)."""
    return pair_mean(cases)


def three_scores(cases: Sequence[CaseScore]) -> ThreeScores:
    """Skor kasus dev, kasus test, dan gabungan, dihitung saat diminta dan tidak disimpan.

    Gabungan adalah rata-rata seluruh kasus, bukan rata-rata dari dev dan test, supaya
    komposisi dev dan test yang timpang tidak diam-diam menyamakan bobot keduanya.
    """
    dev = [kasus for kasus in cases if kasus.split_tag is SplitTag.DEV]
    test = [kasus for kasus in cases if kasus.split_tag is SplitTag.TEST]
    return ThreeScores(dev=pair_mean(dev), test=pair_mean(test), combined=pair_mean(list(cases)))


def category_breakdown(cases: Sequence[CaseScore]) -> tuple[CategoryScore, ...]:
    """Pecahan per kategori dari snapshot. Kasus tanpa kategori masuk "Lain-lain".

    Urutannya kategori bernama dulu menurut abjad, lalu "Lain-lain" di akhir, supaya
    sisa yang tidak berkategori tidak menyela daftar.
    """
    kelompok: dict[str, list[CaseScore]] = {}
    for kasus in cases:
        nama = _nama_kategori(kasus.category)
        kelompok.setdefault(nama, []).append(kasus)

    bernama = sorted(nama for nama in kelompok if nama != LAIN_LAIN)
    urutan = [*bernama, *([LAIN_LAIN] if LAIN_LAIN in kelompok else [])]
    return tuple(
        CategoryScore(
            category=nama, case_count=len(kelompok[nama]), scores=pair_mean(kelompok[nama])
        )
        for nama in urutan
    )


def mark_latest(results: Sequence[GradingResult]) -> tuple[GradingRecord, ...]:
    """Tandai hasil terbaru per percobaan tanpa membuang satu pun hasil lama (AC7).

    Urutan masukan dipertahankan supaya riwayat terbaca apa adanya. Bila dua hasil
    satu percobaan punya graded_at sama, yang belakangan masuk dianggap terbaru.
    """
    terbaru: dict[uuid.UUID, int] = {}
    for indeks, hasil in enumerate(results):
        juara = terbaru.get(hasil.attempt_id)
        if juara is None or hasil.graded_at >= results[juara].graded_at:
            terbaru[hasil.attempt_id] = indeks
    pemenang = set(terbaru.values())
    return tuple(
        GradingRecord(result=hasil, is_latest=indeks in pemenang)
        for indeks, hasil in enumerate(results)
    )


def latest_results(results: Sequence[GradingResult]) -> tuple[GradingResult, ...]:
    """Hanya hasil terbaru tiap percobaan, untuk dipakai menghitung skor kasus dan produk."""
    return tuple(catatan.result for catatan in mark_latest(results) if catatan.is_latest)


def needs_regrade(
    results: Sequence[GradingResult],
    rubric: RubricVersion = RUBRIC_V1,
) -> tuple[uuid.UUID, ...]:
    """Percobaan yang hasil terbarunya masih memakai rubrik lama, urut kemunculannya.

    Hasil lama tidak dihapus dan tidak dihitung ulang di tempat: menilai ulang membuat
    baris baru, dan baris lama tetap memakai rubrik lamanya (AC7).
    """
    return tuple(
        hasil.attempt_id
        for hasil in latest_results(results)
        if hasil.rubric_version != rubric.version
    )


def _nama_kategori(category: str | None) -> str:
    bersih = (category or "").strip()
    return bersih if bersih else LAIN_LAIN


def _dua_digit(nilai: Decimal) -> Decimal:
    return nilai.quantize(_DUA_DIGIT, rounding=ROUND_HALF_UP)
