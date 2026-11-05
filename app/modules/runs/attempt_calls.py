"""Detail teknis setiap upaya panggilan satu percobaan. PBI-12 AC3, SCRUM-218.

Tombol "lihat detail teknis" (D5 langkah 33-38): setiap baris call_logs percobaan
ini, urut try_no, termasuk upaya dari Coba ulang. raw_response adalah respons asli
penyedia sebagai bukti; ringkasan galat diambil dari catatan worker (#98,
raw._execution.error), karena call_logs belum punya kolom error_summary.

D10 bagian C: untuk Developer AI, detail teknis error kasus test disamarkan di
server. Yang tersisa hanya jenis kegagalan dan waktunya (D5 langkah 37).
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.models import CallLog
from app.modules.runs.schemas import AttemptCalls, CallAttemptLog
from app.shared.exceptions import NotFoundError
from app.shared.security import Role


def attempt_calls(
    db: Session, session_id: uuid.UUID, attempt_id: uuid.UUID, *, viewer_role: Role
) -> AttemptCalls:
    if not repository.session_exists(db, session_id):
        raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")
    found = repository.attempt_with_calls(db, session_id, attempt_id)
    if found is None:
        raise NotFoundError("Percobaan tidak ditemukan di sesi ini.", code="ATTEMPT_NOT_FOUND")
    attempt, body, calls = found
    split_tag = (body.get("sections") or {}).get("split_tag")
    masked = viewer_role == Role.DEVELOPER_AI and split_tag == "test"
    return AttemptCalls(
        attempt_id=attempt.id,
        repetition_no=attempt.repetition_no,
        status=attempt.status,
        masked=masked,
        calls=[_upaya(call, masked=masked) for call in calls],
    )


def _upaya(call: CallLog, *, masked: bool) -> CallAttemptLog:
    eksekusi = (call.raw or {}).get("_execution") or {}
    return CallAttemptLog(
        try_no=call.try_no,
        is_manual_retry=call.is_manual_retry,
        started_at=call.started_at,
        ended_at=call.ended_at,
        duration_ms=call.duration_ms,
        result=call.result,
        failure_kind=call.failure_kind,
        model_version=call.model_version,
        error_summary=None if masked else eksekusi.get("error"),
        raw_response=None if masked else call.raw,
    )
