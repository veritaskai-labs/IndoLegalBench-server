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
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.cases.models import CaseStatus, SplitTag
from app.modules.cases.validation import (
    ANGKA_PATTERN,
    PASAL_PATTERN,
    PLACEHOLDER_CASE_CODE_PATTERN,
    validate_payload,
)

# Penghubung antar peraturan dalam satu rujukan (PBI-21 AC6). Link pertama
# memakai "none". Label "jo."/"jis." adalah urusan tampilan.
Connector = Literal["none", "jo", "jis"]
# Tingkat rujukan per link (AC4). Keputusan PO 17 Okt: bentuk ada di link,
# karena contoh AC10 memakai pasal di link pertama dan pasal + angka di link kedua.
LinkForm = Literal["name_only", "pasal", "pasal_angka"]
# Rujukan terstruktur atau teks bebas yang disimpan apa adanya (AC7).
LegalRefKind = Literal["structured", "free_text"]


def _strip_whitespace(nilai: str) -> str:
    """Strip surrounding whitespace."""
    return nilai.strip()


def _strip_any(nilai: Any) -> Any:
    """Strip a string before the length and pattern checks run. Other types pass through."""
    if isinstance(nilai, str):
        return nilai.strip()
    return nilai


def _blank_to_none(nilai: Any) -> Any:
    """Strip a string, and turn a blank one into None, before the pattern check runs."""
    if isinstance(nilai, str):
        return nilai.strip() or None
    return nilai


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


class _LegalRefLinkBase(BaseModel):
    """Fields shared by the write and read shape of one regulation in a citation (PBI-21)."""

    form: LinkForm
    connector: Connector
    regulation_type: str = Field(
        min_length=1,
        max_length=150,
        description="Kode dari GET /regulation-types, bukan label",
    )
    # Panjang mengikuti kolom legal_ref_links (SCRUM-159): number 100, angka 40.
    number: str = Field(min_length=1, max_length=100, description="Boleh alfanumerik")
    about: str | None = Field(default=None, max_length=500, description="Tentang, opsional")
    pasal: str | None = Field(default=None, max_length=40)
    angka: str | None = Field(default=None, max_length=40)
    ayat: str | None = Field(default=None, max_length=20)
    huruf: str | None = Field(default=None, max_length=8)

    @field_validator("regulation_type", "number", mode="before")
    @classmethod
    def strip_required(cls, nilai: Any) -> Any:
        """Strip before min_length, so a whitespace-only value is rejected as blank."""
        return _strip_any(nilai)

    @field_validator("about", "pasal", "angka", "ayat", "huruf", mode="before")
    @classmethod
    def clean_optional(cls, nilai: Any) -> Any:
        """Store a stripped optional field, or None when it is blank."""
        return _blank_to_none(nilai)


class LegalRefLinkWrite(_LegalRefLinkBase):
    """One regulation in a citation, as the editor sends it.

    Cross-field rules (which form needs pasal or angka, the jo./jis. chain,
    year not after the current year) live in validation.py.
    """

    year: int = Field(ge=1, le=9999)
    pasal: str | None = Field(default=None, max_length=40, pattern=PASAL_PATTERN)
    angka: str | None = Field(default=None, max_length=40, pattern=ANGKA_PATTERN)


class LegalRefLinkRead(_LegalRefLinkBase):
    """One regulation in a citation, as stored.

    A migrated Sprint 1 citation may have no year and a free-form pasal (AC9).
    The Author fixes both when the case is saved again.
    """

    year: int | None = None


class _LegalRefBase(BaseModel):
    """Fields shared by the write and read shape of one citation (PBI-21)."""

    kind: LegalRefKind
    free_text: str | None = Field(
        default=None,
        max_length=2000,
        description="Hanya untuk kind free_text, disimpan apa adanya",
    )

    @field_validator("free_text", mode="before")
    @classmethod
    def clean_free_text(cls, nilai: Any) -> Any:
        """Store stripped free text, or None when it is blank."""
        return _blank_to_none(nilai)


class LegalRefWrite(_LegalRefBase):
    """One citation as the editor sends it. Links are in chain order."""

    links: list[LegalRefLinkWrite]


class LegalRefStored(_LegalRefBase):
    """One citation as stored, before the server writes its display_text."""

    links: list[LegalRefLinkRead]


class LegalRefRead(LegalRefStored):
    """One citation as returned, written in legal style by the server (AC6)."""

    display_text: str = Field(
        description='Ditulis server, misalnya "Pasal 156 UU 13/2003 jo. Pasal II Angka 5 UU 6/2023"',
    )


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
    legal_refs: list[LegalRefWrite] = Field(
        min_length=1,
        description="Minimal satu rujukan. Isi wajib tiap link mengikuti bentuknya (PBI-21).",
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
    legal_refs: list[LegalRefRead]
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
    legal_refs: list[LegalRefRead]
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


class ReviewCopy(BaseModel):
    """One version as a reviewer reads it (PBI-6, SCRUM-145).

    split_tag is shown read-only and is not graded (PBI-9 AC2).
    """

    case_id: uuid.UUID
    case_code: str
    author_id: uuid.UUID
    case_version_id: uuid.UUID
    version_no: int
    split_tag: SplitTag
    identity: CaseIdentity
    legal_refs: list[LegalRefRead]
    answer_criteria: dict[str, Any]
    traps: list[dict[str, Any]]
