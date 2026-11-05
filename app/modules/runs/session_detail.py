"""Detail sesi pengukuran dengan jawaban mentah. PBI-11 AC8, SCRUM-210.

Kasus dibaca dari body snapshot (bentuk PBI-8, cases.sections.snapshot_body),
bukan dari kasus live, supaya detail menunjukkan versi yang benar-benar diukur.

Developer AI tidak melihat isi kasus test (SCRUM-183). Barisnya tetap ada dengan
status percobaan, tetapi kode, judul, pertanyaan, dan jawabannya disamarkan
dengan teks yang sama seperti daftar kasus.

Selama sesi antre atau berjalan, Developer AI juga tidak mendapat jawaban mentah
(D10 "hasil sesi berjalan" hanya Admin). Status dan progres tetap terlihat.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.modules.cases.schemas import VersionSections
from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.modules.runs.models import AttemptStatus, StopReason
from app.modules.runs.schemas import AttemptResult, SessionCaseResult, SessionDetail
from app.modules.runs.session_list import NameLookup, SessionRow, to_list_item
from app.modules.runs.session_rules import has_ended
from app.shared.exceptions import NotFoundError
from app.shared.security import Role


@dataclass(frozen=True)
class AttemptRow:
    repetition_no: int
    status: AttemptStatus
    answer_text: str | None
    model_version: str | None


@dataclass(frozen=True)
class CaseRow:
    snapshot_item_id: uuid.UUID
    case_id: uuid.UUID
    body: Mapping[str, Any]
    attempts: list[AttemptRow]


@dataclass(frozen=True)
class SessionDetailRow:
    summary: SessionRow
    purpose: str
    split_filter: str
    snapshot_created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    archived_at: datetime | None
    stopped_at: datetime | None
    stop_reason: StopReason | None
    accepted_as_is_at: datetime | None
    accepted_as_is_by: uuid.UUID | None
    accepted_as_is_reason: str | None
    cases: list[CaseRow]
    resumed_after_long_pause: bool = False
    model_version_unverified: bool = False
    resume_error_code: str | None = None


class SessionDetailStore(Protocol):
    def detail(self, session_id: uuid.UUID) -> SessionDetailRow | None: ...


class SessionDetailService:
    def __init__(self, sessions: SessionDetailStore, names: NameLookup) -> None:
        self._sessions = sessions
        self._names = names

    def get(self, session_id: uuid.UUID, *, viewer_role: Role) -> SessionDetail:
        row = self._sessions.detail(session_id)
        if row is None:
            raise NotFoundError("Pengukuran tidak ditemukan.", code="MEASUREMENT_NOT_FOUND")
        summary = row.summary
        header = to_list_item(
            summary,
            self._names.product_names({summary.product_id}),
            self._names.user_names({summary.created_by}),
        )
        developer_ai = viewer_role == Role.DEVELOPER_AI
        # D10: hasil sesi yang belum berakhir hanya untuk Admin; progres tetap terlihat.
        hide_answers = developer_ai and not has_ended(summary.status)
        cases = sorted(
            (_case(case, hide_test=developer_ai, hide_answers=hide_answers) for case in row.cases),
            key=lambda case: (case.masked, case.case_code),
        )
        return SessionDetail(
            **header.model_dump(),
            purpose=row.purpose,
            split_filter=row.split_filter,
            snapshot_created_at=row.snapshot_created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            archived_at=row.archived_at,
            stopped_at=row.stopped_at,
            stop_reason=row.stop_reason,
            accepted_as_is_at=row.accepted_as_is_at,
            accepted_as_is_by=row.accepted_as_is_by,
            accepted_as_is_reason=row.accepted_as_is_reason,
            resumed_after_long_pause=row.resumed_after_long_pause,
            model_version_unverified=row.model_version_unverified,
            resume_error_code=row.resume_error_code,
            cases=cases,
        )


def _case(case: CaseRow, *, hide_test: bool, hide_answers: bool) -> SessionCaseResult:
    sections = case.body.get("sections") or {}
    split_tag = sections.get("split_tag")
    masked = hide_test and split_tag == "test"
    return SessionCaseResult(
        snapshot_item_id=case.snapshot_item_id,
        case_id=case.case_id,
        case_code=MASKED_CASE_CODE if masked else sections.get("case_code") or "",
        title=MASKED_TITLE if masked else sections.get("identity.title") or "",
        question=None if masked else sections.get("identity.question"),
        split_tag=split_tag,
        masked=masked,
        version_no=None if masked else case.body.get("version_no"),
        sections=None if masked else VersionSections.model_validate(sections),
        attempts=[
            AttemptResult(
                repetition_no=attempt.repetition_no,
                status=attempt.status,
                answer_text=None if masked or hide_answers else attempt.answer_text,
                model_version=attempt.model_version,
            )
            for attempt in case.attempts
        ],
    )
