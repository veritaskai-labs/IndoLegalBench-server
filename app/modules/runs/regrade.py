"""Menilai ulang satu sesi dengan rubrik terbaru. PBI-14 AC7, SCRUM-225.

Menilai ulang tidak pernah mengubah atau menghapus baris hasil penilaian. Ia menambah
baris baru, lalu baris lama berhenti menjadi yang terbaru tanpa kehilangan rubrik,
waktu, maupun skornya sendiri. Dengan begitu riwayat penilaian tetap bisa dibaca apa
adanya dan angka lama tetap bisa dipertanggungjawabkan dengan rubrik saat itu.

Layanan ini hanya berbicara lewat port, jadi ia tidak mengenal tabel maupun penilainya.
Adapter penyimpanannya menunggu tabel grading_results milik SCRUM-222, dan adapter
penilainya memakai judge milik SCRUM-224.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.runs.score_aggregation import (
    RUBRIC_V1,
    GradingRecord,
    GradingResult,
    RubricVersion,
    ScorePair,
    mark_latest,
    needs_regrade,
)


class GradingStorePort(Protocol):
    def results(self, session_id: uuid.UUID) -> tuple[GradingResult, ...]:
        """Seluruh hasil penilaian sesi, termasuk yang sudah tidak terbaru."""
        raise NotImplementedError

    def append(self, session_id: uuid.UUID, results: tuple[GradingResult, ...]) -> None:
        """Tambah baris hasil penilaian. Tidak boleh mengubah atau menghapus baris lama."""
        raise NotImplementedError


class AttemptGraderPort(Protocol):
    def grade(self, attempt_id: uuid.UUID, rubric: RubricVersion) -> ScorePair:
        """Skor satu percobaan menurut rubrik yang diminta, dua jalur tetap terpisah."""
        raise NotImplementedError


@dataclass(frozen=True)
class RegradeOutcome:
    """Hasil satu kali menilai ulang, lengkap dengan riwayat sesudahnya.

    regraded dan skipped dipisah supaya pemanggilnya bisa melaporkan apa yang berubah
    tanpa membandingkan riwayat sebelum dan sesudah.
    """

    regraded: tuple[uuid.UUID, ...]
    skipped: tuple[uuid.UUID, ...]
    history: tuple[GradingRecord, ...]


class RegradeService:
    """Menilai ulang percobaan yang rubriknya ketinggalan, sekali jalan per sesi."""

    def __init__(
        self,
        *,
        store: GradingStorePort,
        grader: AttemptGraderPort,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._grader = grader
        self._clock = clock

    def regrade(
        self,
        session_id: uuid.UUID,
        rubric: RubricVersion = RUBRIC_V1,
    ) -> RegradeOutcome:
        """Nilai ulang yang perlu, biarkan sisanya, lalu kembalikan riwayat terkini.

        Aman dipanggil berulang: percobaan yang hasil terbarunya sudah memakai rubrik
        ini tidak dinilai lagi, jadi panggilan kedua tidak menambah baris.
        """
        sebelum = self._store.results(session_id)
        tertinggal = needs_regrade(sebelum, rubric=rubric)

        if not tertinggal:
            return RegradeOutcome(
                regraded=(),
                skipped=self._percobaan_terbaru(sebelum),
                history=mark_latest(sebelum),
            )

        dinilai_pada = self._clock()
        baris_baru = tuple(
            GradingResult(
                attempt_id=attempt_id,
                rubric_version=rubric.version,
                graded_at=dinilai_pada,
                scores=self._grader.grade(attempt_id, rubric),
            )
            for attempt_id in tertinggal
        )
        self._store.append(session_id, baris_baru)

        ketinggalan = set(tertinggal)
        return RegradeOutcome(
            regraded=tertinggal,
            skipped=tuple(
                attempt_id
                for attempt_id in self._percobaan_terbaru(sebelum)
                if attempt_id not in ketinggalan
            ),
            history=mark_latest((*sebelum, *baris_baru)),
        )

    @staticmethod
    def _percobaan_terbaru(results: tuple[GradingResult, ...]) -> tuple[uuid.UUID, ...]:
        """Id percobaan yang muncul di hasil, urut kemunculan pertamanya, tanpa duplikat."""
        terlihat: dict[uuid.UUID, None] = {}
        for hasil in results:
            terlihat.setdefault(hasil.attempt_id, None)
        return tuple(terlihat)
