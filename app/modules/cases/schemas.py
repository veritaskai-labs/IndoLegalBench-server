"""Bentuk request dan response modul cases.

Schema di sini yang menjadi sumber kontrak OpenAPI. Aturan isian
didelegasikan ke validation.py supaya POST, PUT, dan kelengkapan
memakai definisi yang sama.

TODO(SCRUM-103): Case shape follows that ticket's field list, not a signed contract.
TODO(Klarifikasi #7): case_code pattern below is temporary.
PHK-001 and phk-001 are both allowed today.
"""

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.cases.models import CaseStatus, SplitTag
from app.modules.cases.validation import PLACEHOLDER_CASE_CODE_PATTERN, validate_payload


def _strip_whitespace(nilai: str) -> str:
    """Strip surrounding whitespace."""
    return nilai.strip()


class CaseIdentity(BaseModel):
    """Title, question, and optional category for one case."""

    title: str = Field(min_length=1, max_length=300, description="Judul pertanyaan")
    question: str = Field(min_length=1, max_length=20000, description="Pertanyaan hukum")
    category: str | None = Field(default=None, max_length=120)

    @field_validator("title", "question")
    @classmethod
    def wajib_berisi(cls, nilai: str) -> str:
        """Strip title and question. A whitespace-only value is rejected later by validate_payload."""
        return _strip_whitespace(nilai)

    @field_validator("category")
    @classmethod
    def clean_category(cls, nilai: str | None) -> str | None:
        """Store a stripped category, or None when the value is blank."""
        if nilai is None:
            return None
        bersih = nilai.strip()
        return bersih or None


class LegalRef(BaseModel):
    """One citation. regulation_type, regulation_number, and pasal are required."""

    regulation_type: str = Field(min_length=1, max_length=40)
    regulation_number: str = Field(min_length=1, max_length=40)
    year: int | None = Field(default=None, ge=1, le=9999)
    pasal: str = Field(min_length=1, max_length=40)
    ayat: str | None = Field(default=None, max_length=20)
    huruf: str | None = Field(default=None, max_length=8)

    @field_validator("regulation_type", "regulation_number", "pasal")
    @classmethod
    def wajib_berisi(cls, nilai: str) -> str:
        """Strip the required citation fields."""
        return _strip_whitespace(nilai)

    @field_validator("ayat", "huruf")
    @classmethod
    def clean_optional(cls, nilai: str | None) -> str | None:
        """Store a stripped optional field, or None when it is blank."""
        if nilai is None:
            return None
        bersih = nilai.strip()
        return bersih or None


class AnswerCriteria(BaseModel):
    """Phrases the answer must or must not contain, plus an expected conclusion."""

    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    expected_conclusion: str | None = None


class Trap(BaseModel):
    """An optionally included known mistake and how the model is expected to handle it."""

    description: str = Field(min_length=1, max_length=2000)
    expected_model_behavior: str | None = Field(default=None, max_length=2000)

    @field_validator("description")
    @classmethod
    def require_text(cls, nilai: str) -> str:
        """Strip the trap description. A blank string fails min_length."""
        return _strip_whitespace(nilai)


class CaseWrite(BaseModel):
    """Body for create and update. The server sets status; clients cannot send it."""

    # TODO(Klarifikasi #7): pattern is temporary, not the final case_code rule.
    # PHK-001 and phk-001 are both allowed today.
    case_code: str = Field(
        min_length=2,
        max_length=64,
        pattern=PLACEHOLDER_CASE_CODE_PATTERN,
        description=(
            "Pola sementara, bukan pola final: diawali huruf atau angka, "
            "lalu huruf, angka, titik, garis bawah, atau tanda hubung."
        ),
    )
    identity: CaseIdentity
    legal_refs: list[LegalRef] = Field(
        min_length=1,
        description="Minimal satu rujukan, masing-masing sampai level pasal.",
    )
    answer_criteria: AnswerCriteria = Field(default_factory=AnswerCriteria)
    traps: list[Trap] = Field(default_factory=list)
    split_tag: SplitTag

    @field_validator("case_code", mode="before")
    @classmethod
    def clean_case_code(cls, nilai: Any) -> Any:
        """Strip case_code when the client sent a string."""
        if isinstance(nilai, str):
            return nilai.strip()
        return nilai

    @model_validator(mode="after")
    def apply_shared_rules(self) -> Self:
        """Run the shared save rules so create, update, and completeness agree."""
        validate_payload(self.model_dump(mode="json"))
        return self


class CaseRead(BaseModel):
    """Full case returned by create, read, and update."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    case_code: str
    identity: CaseIdentity
    legal_refs: list[LegalRef]
    answer_criteria: AnswerCriteria
    traps: list[Trap]
    split_tag: SplitTag
    status: CaseStatus
    completeness_pct: int = Field(ge=0, le=100)
    version: int
    created_at: datetime
    updated_at: datetime


class CaseSummary(BaseModel):
    """Short list item. The list endpoint does not return the full body."""

    id: uuid.UUID
    case_code: str
    title: str
    split_tag: SplitTag
    status: CaseStatus
    completeness_pct: int = Field(ge=0, le=100)
    updated_at: datetime


class CompletenessIssue(BaseModel):
    """Satu bagian yang belum terisi, dengan pesan untuk ditampilkan editor."""

    field: str
    message: str


class CaseCompleteness(BaseModel):
    """Indikator kelengkapan satu kasus (SCRUM-107)."""

    is_complete: bool
    ready_for_review: bool
    pct: int = Field(ge=0, le=100)
    missing: list[CompletenessIssue]
    trap_count: int
    legal_ref_count: int


class ActorRead(BaseModel):
    """Who wrote a version, or who froze a suite snapshot."""

    id: uuid.UUID
    name: str


class VersionSummary(BaseModel):
    """One row of GET /cases/{id}/versions."""

    version_no: int
    status: CaseStatus
    author: ActorRead
    created_at: datetime
    changed: list[str]


class VersionSections(BaseModel):
    """One version split into the eight SCRUM-137 sections.

    The dotted names are the API keys. See app/modules/cases/sections.py.
    """

    model_config = ConfigDict(populate_by_name=True)

    identity_title: str = Field(alias="identity.title")
    identity_question: str = Field(alias="identity.question")
    category: str | None = None
    case_code: str
    split_tag: str
    legal_refs: list[Any]
    answer_criteria: dict[str, Any]
    traps: list[Any]


class VersionSide(BaseModel):
    """One side of a compare. sections uses the eight section names."""

    version_no: int
    status: CaseStatus
    author: ActorRead
    created_at: datetime
    sections: VersionSections


class VersionCompare(BaseModel):
    """GET /cases/{id}/versions/compare. a and b are version numbers."""

    a: VersionSide
    b: VersionSide
    changed: list[str]


class ReviewSubmission(BaseModel):
    """POST /cases/{id}/submit-review. The version now waits for reviewers."""

    case_id: uuid.UUID
    version: int = Field(description="Nomor versi yang diajukan")
    status: CaseStatus
    round_no: int = Field(description="1 untuk pengajuan pertama, naik setiap pengajuan ulang")
    round_status: str = Field(
        description="awaiting_assignment sampai dua reviewer terpasang (SCRUM-144)"
    )


class CaseNotReadyBody(BaseModel):
    """422 CASE_NOT_READY: every section that blocks the submission."""

    code: str = Field(examples=["CASE_NOT_READY"])
    message: str
    missing: list[CompletenessIssue]
