"""Schema Pydantic untuk modul reviews.

Sumber kontrak OpenAPI untuk penggantian reviewer dan antrean penugasan ulang.
PBI-6 AC7 dan AC8 (SCRUM-147).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.reviews.models import (
    AssignmentSource,
    AssignmentStatus,
    RoundStatus,
)


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
