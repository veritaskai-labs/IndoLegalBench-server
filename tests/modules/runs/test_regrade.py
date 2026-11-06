"""Menilai ulang satu sesi dengan rubrik terbaru. PBI-14 AC7, SCRUM-225.

Menilai ulang selalu menambah baris, tidak pernah mengubah atau menghapus baris lama,
supaya riwayat penilaian tetap utuh dan hasil lama tetap memakai rubriknya sendiri.

Setiap tes diberi label di docstring-nya:
- Positif: perilaku yang memang diharapkan terjadi.
- Negatif: masukan yang harus ditolak.
- Edge: batas atau kasus pinggir.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest
from app.modules.runs.regrade import RegradeService

from app.modules.runs.score_aggregation import (
    RUBRIC_V1,
    GradingResult,
    RubricAspect,
    RubricVersion,
    ScorePair,
)

RUBRIC_V2 = RubricVersion(
    version="v2",
    max_aspect_score=Decimal(5),
    weights=MappingProxyType(dict.fromkeys(RubricAspect, Decimal(1))),
)

SESI = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _hasil(attempt_id: uuid.UUID, versi: str, jam: int) -> GradingResult:
    return GradingResult(
        attempt_id=attempt_id,
        rubric_version=versi,
        graded_at=datetime(2026, 11, 6, jam, 0, tzinfo=UTC),
        scores=ScorePair(deterministic=Decimal("0.50"), judge=Decimal("0.75")),
    )


class FakeStore:
    """Penyimpan hasil penilaian di memori yang mencatat setiap panggilan tulis."""

    def __init__(self, awal: list[GradingResult] | None = None) -> None:
        self._baris = list(awal or [])
        self.panggilan_tulis: list[tuple[GradingResult, ...]] = []

    def results(self, session_id: uuid.UUID) -> tuple[GradingResult, ...]:
        return tuple(self._baris)

    def append(self, session_id: uuid.UUID, results: tuple[GradingResult, ...]) -> None:
        self.panggilan_tulis.append(results)
        self._baris.extend(results)


class FakeGrader:
    """Penilai palsu yang mencatat percobaan mana saja yang diminta dinilai."""

    def __init__(self) -> None:
        self.diminta: list[uuid.UUID] = []

    def grade(self, attempt_id: uuid.UUID, rubric: RubricVersion) -> ScorePair:
        self.diminta.append(attempt_id)
        return ScorePair(deterministic=Decimal("1.00"), judge=Decimal("0.90"))


def _layanan(store: FakeStore, grader: FakeGrader, jam: int = 12) -> RegradeService:
    return RegradeService(
        store=store,
        grader=grader,
        clock=lambda: datetime(2026, 11, 6, jam, 0, tzinfo=UTC),
    )


# Menilai ulang percobaan yang rubriknya ketinggalan


def test_percobaan_dengan_rubrik_lama_dinilai_ulang():
    """Positif: percobaan yang hasil terbarunya masih rubrik lama diminta dinilai ulang."""
    percobaan = uuid.uuid4()
    store = FakeStore([_hasil(percobaan, "v1", 9)])
    grader = FakeGrader()

    hasil = _layanan(store, grader).regrade(SESI, rubric=RUBRIC_V2)

    assert grader.diminta == [percobaan]
    assert hasil.regraded == (percobaan,)


def test_percobaan_yang_sudah_rubrik_terbaru_dilewati():
    """Positif: yang sudah memakai rubrik terbaru tidak dinilai lagi, jadi aman diulang."""
    percobaan = uuid.uuid4()
    store = FakeStore([_hasil(percobaan, "v2", 9)])
    grader = FakeGrader()

    hasil = _layanan(store, grader).regrade(SESI, rubric=RUBRIC_V2)

    assert grader.diminta == []
    assert hasil.regraded == ()
    assert hasil.skipped == (percobaan,)
    assert store.panggilan_tulis == []


def test_sesi_tanpa_hasil_tidak_menulis_apa_pun():
    """Edge: sesi yang belum pernah dinilai tidak memaksa penulisan baris kosong."""
    store = FakeStore()
    grader = FakeGrader()

    hasil = _layanan(store, grader).regrade(SESI, rubric=RUBRIC_V2)

    assert hasil.regraded == ()
    assert hasil.skipped == ()
    assert store.panggilan_tulis == []


def test_baris_baru_memakai_rubrik_dan_waktu_dari_clock():
    """Positif: baris hasil penilaian ulang memakai rubrik baru dan waktu dari clock."""
    percobaan = uuid.uuid4()
    store = FakeStore([_hasil(percobaan, "v1", 9)])

    _layanan(store, FakeGrader(), jam=15).regrade(SESI, rubric=RUBRIC_V2)

    (baris,) = store.panggilan_tulis[0]
    assert baris.rubric_version == "v2"
    assert baris.graded_at == datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
    assert baris.scores == ScorePair(deterministic=Decimal("1.00"), judge=Decimal("0.90"))


def test_semua_baris_baru_ditulis_dalam_satu_panggilan():
    """Edge: tiga percobaan menghasilkan satu panggilan tulis, bukan tiga."""
    percobaan = [uuid.uuid4() for _ in range(3)]
    store = FakeStore([_hasil(p, "v1", 9) for p in percobaan])

    _layanan(store, FakeGrader()).regrade(SESI, rubric=RUBRIC_V2)

    assert len(store.panggilan_tulis) == 1
    assert len(store.panggilan_tulis[0]) == 3


# Riwayat tetap utuh


def test_hasil_lama_tetap_ada_dan_tidak_lagi_terbaru():
    """Positif: setelah dinilai ulang, baris lama tetap tersimpan tapi bukan yang terbaru."""
    percobaan = uuid.uuid4()
    lama = _hasil(percobaan, "v1", 9)
    store = FakeStore([lama])

    hasil = _layanan(store, FakeGrader(), jam=15).regrade(SESI, rubric=RUBRIC_V2)

    riwayat = hasil.history
    assert len(riwayat) == 2
    assert [catatan.result.rubric_version for catatan in riwayat] == ["v1", "v2"]
    assert [catatan.is_latest for catatan in riwayat] == [False, True]


def test_hasil_lama_tetap_memakai_rubriknya_sendiri():
    """Positif: rubrik pada baris lama tidak ditimpa rubrik baru."""
    percobaan = uuid.uuid4()
    store = FakeStore([_hasil(percobaan, "v1", 9)])

    hasil = _layanan(store, FakeGrader(), jam=15).regrade(SESI, rubric=RUBRIC_V2)

    lama = next(c.result for c in hasil.history if not c.is_latest)
    assert lama.rubric_version == "v1"
    assert lama.graded_at == datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
    assert lama.scores == ScorePair(deterministic=Decimal("0.50"), judge=Decimal("0.75"))


def test_percobaan_lain_di_sesi_yang_sama_tidak_tersentuh():
    """Edge: percobaan yang sudah terbaru tidak ikut menambah baris saat yang lain dinilai ulang."""
    tertinggal = uuid.uuid4()
    terkini = uuid.uuid4()
    store = FakeStore([_hasil(tertinggal, "v1", 9), _hasil(terkini, "v2", 10)])

    hasil = _layanan(store, FakeGrader(), jam=15).regrade(SESI, rubric=RUBRIC_V2)

    assert hasil.regraded == (tertinggal,)
    assert hasil.skipped == (terkini,)
    baris_terkini = [c for c in hasil.history if c.result.attempt_id == terkini]
    assert len(baris_terkini) == 1


def test_menilai_ulang_dua_kali_hanya_menambah_sekali():
    """Edge: panggilan kedua dengan rubrik yang sama tidak menambah baris lagi."""
    percobaan = uuid.uuid4()
    store = FakeStore([_hasil(percobaan, "v1", 9)])
    layanan = _layanan(store, FakeGrader(), jam=15)

    layanan.regrade(SESI, rubric=RUBRIC_V2)
    kedua = layanan.regrade(SESI, rubric=RUBRIC_V2)

    assert kedua.regraded == ()
    assert len(store.panggilan_tulis) == 1
    assert len(store.results(SESI)) == 2


def test_rubrik_bawaan_adalah_rubrik_v1():
    """Edge: tanpa argumen rubric, layanan memakai RUBRIC_V1 seperti modul agregasi."""
    percobaan = uuid.uuid4()
    store = FakeStore([_hasil(percobaan, "v0", 9)])
    grader = FakeGrader()

    _layanan(store, grader).regrade(SESI)

    assert grader.diminta == [percobaan]
    assert store.panggilan_tulis[0][0].rubric_version == RUBRIC_V1.version


def test_port_wajib_lewat_kata_kunci():
    """Negatif: port tidak boleh dipasang secara posisional, supaya pemasangannya terbaca."""
    with pytest.raises(TypeError):
        RegradeService(FakeStore(), FakeGrader(), lambda: datetime.now(UTC))  # type: ignore[misc]
