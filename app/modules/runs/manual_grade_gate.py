"""Prasyarat Nilai dan status Dinilai untuk sesi manual. PBI-22 (SCRUM-176) AC9; SCRUM-233.

Dipindah dari PBI-14 AC6: POST /measurements/{id}/grade (PBI-14-BE-5, SCRUM-226) untuk sesi
manual hanya bila semua kasus dan percobaan terisi, kalau belum 409 NOT_COMPLETE (D7). Setelah
dinilai, Lengkap menjadi Dinilai (D8). Penilaiannya sendiri memakai pipeline, rubrik, dan
versi rubrik yang sama dengan jawaban otomatis (AC9); itu milik grade_session di SCRUM-226.

grade_session memanggil dua titik ini, di transaksi yang sama dengan hasil penilaian:

    gate = ManualGradeGate(ManualSessionRepository(db))
    if gate.before_grading(session_id) is None:
        ensure_session_gradable(session)   # sesi otomatis: aturan D4 dan arsip (#114)
    ...  # nilai dengan judge
    ensure_unchanged_since_load(db, session_id, cases)   # kunci sesi, cek arsip dan jawaban
    ...  # simpan hasil
    if response.status == RUN_COMPLETED:   # run tanpa skor gagal (#128), sesi tetap Lengkap
        gate.after_grading(session_id, now=now)   # tidak berbuat apa pun untuk sesi otomatis
    db.commit()

before_grading tidak mengunci (review #121): penilaian memanggil LLM judge untuk setiap
percobaan, dan kunci FOR UPDATE selama itu menahan semua simpan jawaban dan unggah bukti.
Baris sesi baru dikunci setelah judge selesai. Di situ arsip dan isi jawaban diperiksa ulang
(review Elision #121, ensure_unchanged_since_load di session_grade), lalu status dibaca ulang
di after_grading: sesi yang ditandai Selesai selama penilaian tetap Selesai.
"""

import uuid
from datetime import datetime

from app.modules.runs.manual_session_rules import (
    MANUAL_STATUSES,
    ensure_manual_gradable,
    status_after_grading,
)
from app.modules.runs.models import SessionStatus
from app.modules.runs.session_ports import ManualStatusStore
from app.shared.exceptions import ConflictError


def _ensure_manual_status(status: SessionStatus) -> None:
    if status not in MANUAL_STATUSES:
        # Baris manual lama berstatus D4 (sebelum SCRUM-230): 409, bukan 500 (review #116).
        raise ConflictError(
            "Sesi manual ini masih berstatus sesi otomatis dan tidak bisa dinilai.",
            code="MANUAL_SESSION_STATUS_INVALID",
        )


class ManualGradeGate:
    def __init__(self, store: ManualStatusStore) -> None:
        self._store = store

    def before_grading(self, session_id: uuid.UUID) -> SessionStatus | None:
        """None = bukan sesi manual (aturan D4 berlaku); selain itu status sesi yang lengkap."""
        row = self._store.read_manual(session_id)
        if row is None:
            return None
        # Review #121: Selesai bisa diarsipkan; arsip tidak dinilai, sama dengan sesi otomatis.
        if row.archived_at is not None:
            raise ConflictError("Sesi ini sudah diarsipkan.", code="SESSION_ARCHIVED")
        _ensure_manual_status(row.status)
        ensure_manual_gradable(row.status)
        return row.status

    def after_grading(self, session_id: uuid.UUID, *, now: datetime) -> SessionStatus | None:
        """D8: Lengkap -> Dinilai. Tanpa commit: hasil penilaian dan statusnya batal bersama."""
        status = self._store.lock_manual(session_id)
        if status is None:
            return None
        target = status_after_grading(status)
        if target != status:
            self._store.change_status(session_id, target, at=now, commit=False)
        return target
