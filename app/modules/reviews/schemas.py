"""Bentuk request dan response modul reviews. PBI-6, SCRUM-145, SCRUM-147.

Setiap response yang memuat verdict hanya membawa verdict yang boleh
dilihat peminta (visibility.can_see_verdict). Verdict yang tersembunyi
dikirim sebagai null, bukan dihilangkan diam-diam di client.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.cases.schemas import ReviewCopy
from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)


class VerdictWrite(BaseModel):
    """POST /reviews/{assignment_id}/verdict.

    Field lain ditolak (422), termasuk split_tag: reviewer menilai substansi
    kasus, bukan tag dev/test (PBI-9 AC2).
    """

    model_config = ConfigDict(extra="forbid")

    decision: VerdictDecision
    comment: str | None = Field(
        default=None,
        max_length=5000,
        description="Wajib bila decision = revise (COMMENT_REQUIRED)",
    )


class VerdictView(BaseModel):
    """Satu verdict yang boleh dilihat peminta."""

    decision: VerdictDecision
    comment: str | None
    submitted_at: datetime | None


class VerdictResult(VerdictView):
    """201 dari pengiriman verdict: verdict milik sendiri saja (AC3, D3 langkah 26)."""

    assignment_id: uuid.UUID
    round_status: RoundStatus
    round_decision: RoundDecision | None


class AssignedReview(BaseModel):
    """Satu baris GET /reviews/assigned."""

    assignment_id: uuid.UUID
    round_no: int
    assigned_at: datetime | None
    case: ReviewCopy
    my_verdict: VerdictView | None


class PendingReview(BaseModel):
    """Satu baris antrean dashboard reviewer (PBI-19 AC6, SCRUM-154).

    waiting_since adalah saat round dibuka; antrean diurutkan dari yang
    terlama. assigned_at bisa lebih baru bila reviewer ini pengganti.
    """

    assignment_id: uuid.UUID
    case_id: uuid.UUID
    case_code: str
    title: str
    round_no: int
    waiting_since: datetime
    assigned_at: datetime


class ReviewerRead(BaseModel):
    id: uuid.UUID
    name: str


class ReviewerSlot(BaseModel):
    """Satu reviewer aktif di round. verdict null bila belum ada atau belum boleh dilihat."""

    assignment_id: uuid.UUID
    reviewer: ReviewerRead
    status: AssignmentStatus
    verdict: VerdictView | None


class CaseReview(BaseModel):
    """GET /cases/{case_id}/review: round terakhir kasus ini."""

    case_id: uuid.UUID
    case_version_id: uuid.UUID
    round_no: int
    status: RoundStatus
    decision: RoundDecision | None
    opened_at: datetime | None
    decided_at: datetime | None
    reviewers: list[ReviewerSlot]


class InReviewCase(BaseModel):
    """GET /reviews/in-review: satu kasus yang round terakhirnya belum diputuskan (AC3).

    Termasuk versi baru dari kasus yang sudah disetujui; status kasusnya tetap
    approved selama versi itu direview (temuan Pra-UAT F-02). version_no di atas 1
    berarti ada versi lama yang disetujui dan masih berlaku.
    """

    case_id: uuid.UUID
    case_code: str
    title: str
    version_no: int
    review: CaseReview


class ReplaceReviewerRequest(BaseModel):
    """Payload untuk POST /reviews/{assignment_id}/replace."""

    new_reviewer_id: uuid.UUID = Field(
        description="ID reviewer pengganti (harus reviewer aktif, bukan pembuat kasus atau reviewer lain di round ini)"
    )
    reason: str = Field(
        min_length=1,
        description="Alasan penggantian reviewer (wajib untuk jejak audit)",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        bersih = value.strip()
        if not bersih:
            raise ValueError("Alasan tidak boleh kosong")
        return bersih


class ReassignmentAssignmentRead(BaseModel):
    """Ringkasan satu penugasan dalam putaran review."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewer_name: str | None = None
    status: AssignmentStatus
    source: AssignmentSource
    assigned_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None


class ReplaceReviewerResponse(BaseModel):
    """Response sukses POST /reviews/{assignment_id}/replace."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="ID penugasan baru")
    review_round_id: uuid.UUID
    reviewer_id: uuid.UUID
    source: AssignmentSource
    assigned_by: uuid.UUID | None = None
    assigned_at: datetime
    status: AssignmentStatus
    replaced_assignment_id: uuid.UUID = Field(description="ID penugasan lama yang digantikan")


class NeedsReassignmentResponse(BaseModel):
    """Satu kasus pada antrean penugasan ulang (GET /reviews/needs-reassignment)."""

    model_config = ConfigDict(from_attributes=True)

    case_id: uuid.UUID
    case_code: str
    case_title: str
    round_id: uuid.UUID
    round_no: int
    round_status: RoundStatus
    active_reviewers_count: int
    void_assignments: list[ReassignmentAssignmentRead]
    assignments: list[ReassignmentAssignmentRead]


class CommentReplyWrite(BaseModel):
    """K-03: teks biasa; tidak menerima identitas pelaku dari client."""

    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def trim_body(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Balasan tidak boleh kosong")
        return clean


class CommentReplyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    author_id: uuid.UUID
    author_name: str
    author_role: Literal["author", "reviewer"]
    body: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class CommentDiscussion(BaseModel):
    round_no: int
    round_status: RoundStatus
    viewer_id: uuid.UUID
    commented_at: datetime
    assignment_id: uuid.UUID
    case_id: uuid.UUID
    reviewer_name: str
    comment: str
    can_reply: bool
    replies: list[CommentReplyRead]
