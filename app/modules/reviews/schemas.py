"""Bentuk request dan response modul reviews. PBI-6, SCRUM-145.

Setiap response yang memuat verdict hanya membawa verdict yang boleh
dilihat peminta (visibility.can_see_verdict). Verdict yang tersembunyi
dikirim sebagai null, bukan dihilangkan diam-diam di client.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.cases.schemas import ReviewCopy
from app.modules.reviews.models import (
    AssignmentStatus,
    RoundDecision,
    RoundStatus,
    VerdictDecision,
)


class VerdictWrite(BaseModel):
    """POST /reviews/{assignment_id}/verdict."""

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
