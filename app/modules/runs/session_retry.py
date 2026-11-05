"""Coba ulang yang gagal: hanya panggilan gagal yang dibuka lagi. PBI-12 AC3, SCRUM-218.

D4: dari Selesai dengan kegagalan, pengguna mencentang kasus gagal (per kasus
atau semua). Percobaan technical_failure itu kembali pending dan sesi berjalan
lagi; runner (SCRUM-249) hanya memanggil percobaan pending, jadi hasil yang
berhasil tetap terkunci. repetition_no tidak bertambah: upaya baru tercatat di
call_logs sebagai try_no + 1 (CallLogService.record).

D5 langkah 30 menulis status sesi kembali queued; yang dipakai D4 dan tabel
transisi SCRUM-214 (keputusan Rafa di #97): finished_with_failures -> running.

Urutan pemeriksaan: sesi ada (404), Developer AI hanya sesi miliknya (403, D10),
keadaan sesi (409), lalu pilihan percobaan (422). Baris sesi dikunci sampai commit.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.models import AttemptStatus, SessionStatus
from app.modules.runs.schemas import SessionRetryRequest, SessionRetryResponse
from app.modules.runs.session_archive import NOT_YOUR_SESSION
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.shared.security import Role


def retry_failed_attempts(
    db: Session,
    session_id: uuid.UUID,
    request: SessionRetryRequest,
    *,
    actor_id: uuid.UUID,
    actor_role: Role,
    now: datetime,
) -> SessionRetryResponse:
    session = repository.get_session_for_update(db, session_id)
    if session is None:
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")
    if actor_role == Role.DEVELOPER_AI and session.created_by != actor_id:
        raise ForbiddenError(
            "Developer AI hanya bisa mencoba ulang sesi yang ia buat.", code=NOT_YOUR_SESSION
        )
    if session.accepted_as_is_at is not None:
        raise ConflictError(
            "Sesi sudah diterima apa adanya. Hapus tandanya dulu untuk mencoba ulang.",
            code="SESSION_ACCEPTED",
        )
    if session.status != SessionStatus.FINISHED_WITH_FAILURES:
        raise ConflictError(
            "Coba ulang hanya untuk sesi Selesai dengan kegagalan.",
            code="SESSION_NOT_RETRYABLE",
            details={"status": session.status.value},
        )
    attempt_ids = _pilihan(repository.attempt_statuses(db, session_id), request)
    repository.reopen_for_retry(db, session_id, attempt_ids, at=now)
    return SessionRetryResponse(
        id=session_id, status=SessionStatus.RUNNING, retried_attempt_ids=attempt_ids
    )


def _pilihan(
    statuses: dict[uuid.UUID, AttemptStatus], request: SessionRetryRequest
) -> list[uuid.UUID]:
    """Percobaan gagal yang dicoba ulang. Id lain (berhasil atau sesi lain) ditolak semuanya."""
    gagal = [
        attempt_id
        for attempt_id, status in statuses.items()
        if status == AttemptStatus.TECHNICAL_FAILURE
    ]
    if request.all_failed:
        if not gagal:
            raise ValidationError(
                "Tidak ada percobaan gagal yang bisa dicoba ulang.", code="NO_FAILED_ATTEMPTS"
            )
        return gagal
    dipilih = list(dict.fromkeys(request.attempt_ids or []))
    ditolak = [attempt_id for attempt_id in dipilih if attempt_id not in gagal]
    if ditolak:
        raise ValidationError(
            "Hanya percobaan yang gagal teknis di sesi ini yang bisa dicoba ulang.",
            code="ATTEMPT_NOT_RETRYABLE",
            field="attempt_ids",
            details={"attempt_ids": [str(attempt_id) for attempt_id in ditolak]},
        )
    return dipilih
