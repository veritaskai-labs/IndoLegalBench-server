"""Detail sesi pengukuran dengan jawaban mentah. PBI-11 AC8, SCRUM-210.

Kasus dibaca dari body snapshot (bentuk PBI-8, cases.sections.snapshot_body),
bukan dari kasus live, supaya detail menunjukkan versi yang benar-benar diukur.

Developer AI tidak melihat isi kasus test (SCRUM-183). Barisnya tetap ada dengan
status percobaan, tetapi kode, judul, pertanyaan, dan jawabannya disamarkan
dengan teks yang sama seperti daftar kasus. Keputusannya ada di
case_visibility (fail-closed: tag kosong atau tak dikenal ikut disamarkan).

Jawaban kasus dev terbaca begitu percobaannya selesai, tanpa menunggu sesi
berakhir (PBI-11 AC9, SCRUM-261).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.modules.cases.models import SplitTag
from app.modules.cases.schemas import VersionSections
from app.modules.cases.service import MASKED_CASE_CODE, MASKED_TITLE
from app.modules.runs.case_visibility import can_view_case_content
from app.modules.runs.models import AttemptSource, AttemptStatus, StopReason
from app.modules.runs.schemas import AttemptResult, SessionCaseResult, SessionDetail
from app.modules.runs.session_list import NameLookup, SessionRow, to_list_item
from app.modules.runs.session_rules import is_report_eligible
from app.shared.exceptions import NotFoundError
from app.shared.security import Role


@dataclass(frozen=True)
class AttemptRow:
    id: uuid.UUID
    repetition_no: int
    status: AttemptStatus
    answer_text: str | None
    model_version: str | None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    source: AttemptSource = AttemptSource.AUTOMATIC
    filled_at: datetime | None = None


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
        cases = sorted(
            (_case(case, viewer_role=viewer_role) for case in row.cases),
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
            report_eligible=is_report_eligible(
                summary.status, accepted_as_is_at=row.accepted_as_is_at
            ),
            cases=cases,
        )


def _known_split(split_tag: object) -> SplitTag | None:
    """Tag di luar enum tidak dikembalikan apa adanya, supaya response tetap valid."""
    return SplitTag(split_tag) if split_tag in {tag.value for tag in SplitTag} else None


def _case(case: CaseRow, *, viewer_role: Role) -> SessionCaseResult:
    sections = case.body.get("sections") or {}
    split_tag = sections.get("split_tag")
    # Masking must read the raw split_tag: _known_split maps an unknown tag to None, which
    # would make it look like a dev case.
    masked = not can_view_case_content(viewer_role, split_tag)
    return SessionCaseResult(
        snapshot_item_id=case.snapshot_item_id,
        case_id=case.case_id,
        case_code=MASKED_CASE_CODE if masked else sections.get("case_code") or "",
        title=MASKED_TITLE if masked else sections.get("identity.title") or "",
        question=None if masked else sections.get("identity.question"),
        split_tag=_known_split(split_tag),
        masked=masked,
        version_no=None if masked else case.body.get("version_no"),
        sections=None if masked else VersionSections.model_validate(sections),
        attempts=[
            AttemptResult(
                # Tidak disamarkan: FE perlu id untuk coba ulang per kasus (SCRUM-218).
                id=attempt.id,
                repetition_no=attempt.repetition_no,
                status=attempt.status,
                answer_text=None if masked else attempt.answer_text,
                model_version=attempt.model_version,
                # Waktu tidak memuat isi kasus, jadi tidak ikut disamarkan.
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
            )
            for attempt in case.attempts
        ],
    )
