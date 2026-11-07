"""Status sesi manual: hitung ulang, Tandai selesai, halaman sesi. PBI-22 (SCRUM-176); SCRUM-230.

Aturannya ada di manual_session_rules (murni), query-nya di ManualSessionRepository di
balik port ManualStatusStore (review #116). Service ini hanya merangkai keduanya, jadi
bisa diuji dengan penyimpanan palsu. Status tetap ditulis lewat change_status, satu-satunya
jalan tulis status yang mengikuti tabel transisi dan mencatat audit (PBI-18).
"""

import uuid
from datetime import datetime

from app.modules.runs.manual_session_detail import build_manual_detail
from app.modules.runs.manual_session_rules import (
    MANUAL_STATUSES,
    ManualProgress,
    answer_date_range,
    ensure_finalizable,
    filling_status,
)
from app.modules.runs.models import SessionStatus
from app.modules.runs.schemas import ManualFinalizeResponse, ManualSessionDetail
from app.modules.runs.session_ports import ManualStatusStore
from app.shared.exceptions import ConflictError, NotFoundError


def _not_found() -> NotFoundError:
    return NotFoundError("Sesi pengukuran manual tidak ditemukan.", code="MANUAL_SESSION_NOT_FOUND")


class ManualSessionStatusService:
    def __init__(self, store: ManualStatusStore) -> None:
        self._store = store

    def _locked_status(self, session_id: uuid.UUID) -> SessionStatus:
        status = self._store.lock_manual(session_id)
        if status is None:
            raise _not_found()
        return status

    def recompute(self, session_id: uuid.UUID, *, now: datetime) -> SessionStatus:
        """Status D8 dan rentang tanggal setelah jawaban disimpan. Dipanggil SCRUM-231.

        Tidak commit: jawaban dan statusnya tersimpan atau batal bersama di transaksi
        pemanggil. Baris sesi dikunci, jadi dua pengisi bersamaan tidak saling menimpa.
        """
        current = self._locked_status(session_id)
        if current not in MANUAL_STATUSES:
            # Baris manual lama dengan status D4 (sebelum SCRUM-230): 409, bukan 500 (review #116).
            raise ConflictError(
                "Sesi manual ini masih berstatus sesi otomatis dan tidak bisa diisi.",
                code="MANUAL_SESSION_STATUS_INVALID",
            )
        attempts = self._store.manual_attempts(session_id)
        progress = ManualProgress(filled=sum(a.filled for a in attempts), total=len(attempts))
        target = filling_status(current, progress)
        # AC13: rentang dari jawaban yang terisi saja.
        start, end = answer_date_range(a.answered_at_platform for a in attempts if a.filled)
        self._store.set_answer_dates(session_id, start, end)
        if target != current:
            self._store.change_status(session_id, target, at=now, commit=False)
        return target

    def finalize(
        self, session_id: uuid.UUID, *, finalized_by: uuid.UUID, now: datetime
    ) -> ManualFinalizeResponse:
        """Tandai selesai: hanya dari Lengkap atau Dinilai, sekali, dengan pelakunya (AC8, D9)."""
        ensure_finalizable(self._locked_status(session_id))
        self._store.change_status(
            session_id, SessionStatus.FINALIZED, at=now, finalized_by=finalized_by, commit=True
        )
        return ManualFinalizeResponse(
            id=session_id,
            status=SessionStatus.FINALIZED,
            finalized_at=now,
            finalized_by=finalized_by,
        )

    def detail(
        self, session_id: uuid.UUID, *, unfilled_only: bool, warning_days: int | None
    ) -> ManualSessionDetail:
        row = self._store.read_manual(session_id)
        if row is None:
            raise _not_found()
        return build_manual_detail(
            row,
            self._store.snapshot_cases(session_id),
            self._store.manual_attempts(session_id),
            unfilled_only=unfilled_only,
            warning_days=warning_days,
        )
