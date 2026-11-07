"""Prasyarat Nilai dan status Dinilai untuk sesi manual. PBI-22 (SCRUM-176) AC9; SCRUM-233.

Dipindah dari PBI-14 AC6: POST /measurements/{id}/grade (PBI-14-BE-5, SCRUM-226) untuk sesi
manual hanya bila semua kasus dan percobaan terisi, kalau belum 409 NOT_COMPLETE (D7). Setelah
dinilai, Lengkap menjadi Dinilai (D8). Penilaiannya sendiri memakai pipeline, rubrik, dan
versi rubrik yang sama dengan jawaban otomatis (AC9); itu milik grade_session di SCRUM-226.

grade_session memanggil dua titik ini, di transaksi yang sama dengan hasil penilaian:

    manual_status = ManualGradeGate(ManualSessionRepository(db)).before_grading(session_id)
    if manual_status is None:
        ensure_session_gradable(session)   # aturan D4 untuk sesi otomatis
    ...  # nilai dan simpan hasil
    if manual_status is not None:
        gate.after_grading(session_id, manual_status, now=now)
    db.commit()

before_grading mengunci baris sesi manual, jadi Tandai selesai atau jawaban baru tidak bisa
menyela di tengah penilaian.
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


class ManualGradeGate:
    def __init__(self, store: ManualStatusStore) -> None:
        self._store = store

    def before_grading(self, session_id: uuid.UUID) -> SessionStatus | None:
        """None = bukan sesi manual (aturan D4 berlaku); selain itu status sesi yang lengkap."""
        status = self._store.lock_manual(session_id)
        if status is None:
            return None
        if status not in MANUAL_STATUSES:
            # Baris manual lama berstatus D4 (sebelum SCRUM-230): 409, bukan 500 (review #116).
            raise ConflictError(
                "Sesi manual ini masih berstatus sesi otomatis dan tidak bisa dinilai.",
                code="MANUAL_SESSION_STATUS_INVALID",
            )
        ensure_manual_gradable(status)
        return status

    def after_grading(
        self, session_id: uuid.UUID, status: SessionStatus, *, now: datetime
    ) -> SessionStatus:
        """D8: Lengkap -> Dinilai. Tanpa commit: hasil penilaian dan statusnya batal bersama."""
        target = status_after_grading(status)
        if target != status:
            self._store.change_status(session_id, target, at=now, commit=False)
        return target
