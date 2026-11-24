"""Endpoint HTTP modul cases.

PBI-3, SCRUM-106. Router hanya menerjemahkan HTTP ke pemanggilan
service. Tidak ada logika bisnis dan tidak ada query database di sini.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BeforeValidator
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.cases import service
from app.modules.cases.models import CaseStatus, SplitTag
from app.modules.cases.schemas import (
    CaseCompleteness,
    CaseNotReadyBody,
    CasePage,
    CaseRead,
    CaseSummary,
    CaseWrite,
    ReviewSubmission,
    SplitTagChange,
    TagConfirmationBody,
    VersionCompare,
    VersionSummary,
)
from app.shared.database import get_db
from app.shared.security import REVIEWER_ROLES, WRITER_ROLES, CurrentUser, Role, require_roles
from app.shared.text import strip_blank

router = APIRouter(tags=["cases"])

_can_write = require_roles(*WRITER_ROLES, Role.ADMIN)
# SCRUM-183: Developer AI reads the case list and dev case detail, masked by the service.
_can_read = require_roles(*WRITER_ROLES, Role.ADMIN, Role.DEVELOPER_AI)
# SCRUM-339: the case directory shows unmasked rows, so only writers and Admin.
_can_search = require_roles(*WRITER_ROLES, Role.ADMIN)
# D5a / PBI-24: only the writer submits, and only their own case (checked in the service).
_can_submit = require_roles(*WRITER_ROLES)
# D5a / PBI-9 AC3: after approval only the Admin changes the dev/test tag.
_admin = require_roles(Role.ADMIN)
# SCRUM-138: Viewer sees history, and a Reviewer re-reviews a new version.
_can_read_versions = require_roles(*WRITER_ROLES, *REVIEWER_ROLES, Role.ADMIN, Role.VIEWER)

_ERROR_CODES = {
    403: {"model": ErrorBody, "description": "Bukan pembuat kasus dan bukan admin."},
    404: {"model": ErrorBody, "description": "Kasus atau suite tidak ditemukan."},
    409: {
        "model": ErrorBody,
        "description": (
            "Kode kasus sudah dipakai (`CASE_CODE_TAKEN`), kode diubah setelah "
            "ada versi yang disetujui (`CASE_CODE_LOCKED`), tag diubah lewat PUT "
            "setelah disetujui (`SPLIT_TAG_LOCKED`), versi yang disetujui "
            "atau sedang ditinjau diubah (`VERSION_LOCKED`), versi baru diminta "
            "saat draf atau tinjauan masih berjalan (`VERSION_IN_PROGRESS`), "
            "atau versi baru diminta sebelum ada versi yang disetujui "
            "(`NO_APPROVED_VERSION`)."
        ),
    },
    422: {
        "model": ErrorBody,
        "description": (
            "Suite tidak aktif (`SUITE_NOT_ACTIVE`), tag kosong "
            "(`SPLIT_TAG_REQUIRED`), pola case_code ditolak "
            "(`CASE_CODE_INVALID`), atau field wajib kosong (`field`)."
        ),
    },
}


def _user_id(user: CurrentUser) -> uuid.UUID:
    """Normalize the session user id to UUID. The dependency may hand back a string."""
    if isinstance(user.user_id, uuid.UUID):
        return user.user_id
    return uuid.UUID(str(user.user_id))


@router.post(
    "/suites/{suite_id}/cases",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buat kasus hukum dalam status draft",
    responses=_ERROR_CODES,
)
def create_case(
    suite_id: uuid.UUID,
    payload: CaseWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Create a draft case. Authors and admins only."""
    return service.create_case(db, suite_id, payload, actor_id=_user_id(user))


@router.get(
    "/suites/{suite_id}/cases",
    response_model=list[CaseSummary],
    summary="Daftar ringkas kasus di dalam satu suite",
    responses={404: _ERROR_CODES[404]},
)
def list_cases(
    suite_id: uuid.UUID,
    status_filter: CaseStatus | None = Query(default=None, alias="status"),
    split_tag: SplitTag | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_read),
) -> list[CaseSummary]:
    """List the short case summary for one suite.

    Developer AI gets approved cases only. Test rows come back with
    `masked` = true and placeholder `case_code` and `title`.
    """
    if user.role == Role.DEVELOPER_AI:
        return service.list_cases_for_developer_ai(
            db, suite_id, status=status_filter, split_tag=split_tag
        )
    return service.list_cases(db, suite_id, status=status_filter, split_tag=split_tag)


@router.get(
    "/cases",
    response_model=CasePage,
    summary="Daftar kasus lintas suite dengan pencarian dan filter",
    responses={403: {"model": ErrorBody, "description": "Hanya Kontributor dan Admin."}},
)
def list_all_cases(
    q: Annotated[
        str | None,
        Query(
            max_length=200,
            description=(
                "Sebagian kode kasus atau judul, huruf besar/kecil tidak dibedakan. "
                "Kosong atau hanya spasi berarti tanpa kata kunci."
            ),
        ),
        BeforeValidator(strip_blank),
    ] = None,
    status_filter: CaseStatus | None = Query(
        default=None, alias="status", description="Status versi yang berlaku"
    ),
    suite_id: uuid.UUID | None = Query(
        default=None, description="Satu suite. Suite yang tidak ada memberi daftar kosong."
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_search),
) -> CasePage:
    """List the cases of every suite, newest update first (PBI-4 AC3, SCRUM-339).

    Contributor and Admin only: rows are not masked, so Developer AI keeps the
    per-suite list that hides test cases.
    """
    return service.list_all_cases(
        db, keyword=q, status=status_filter, suite_id=suite_id, page=page, size=size
    )


@router.get(
    "/cases/{case_id}",
    response_model=CaseRead,
    summary="Detail satu kasus",
    responses={
        403: {
            "model": ErrorBody,
            "description": "Developer AI membuka kasus test (`TEST_CASE_HIDDEN`).",
        },
        404: {
            "model": ErrorBody,
            "description": (
                "Kasus tidak ditemukan. Untuk Developer AI juga kasus yang belum disetujui."
            ),
        },
    },
)
def get_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_read),
) -> CaseRead:
    """Return the full case.

    Developer AI gets the approved version of a dev case only.
    """
    if user.role == Role.DEVELOPER_AI:
        return service.get_case_for_developer_ai(db, case_id)
    return service.get_case(db, case_id)


@router.put(
    "/cases/{case_id}",
    response_model=CaseRead,
    summary="Ubah kasus",
    responses=_ERROR_CODES,
)
def update_case(
    case_id: uuid.UUID,
    payload: CaseWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Update the open version. The service rejects an approved version."""
    return service.update_case(
        db,
        case_id,
        payload,
        actor_id=_user_id(user),
        is_admin=user.role == Role.ADMIN,
    )


@router.post(
    "/cases/{case_id}/versions",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buat versi draf dari kasus yang sudah disetujui",
    responses=_ERROR_CODES,
)
def start_case_version(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Copy the approved wording into a new draft. No request body."""
    return service.start_new_version(
        db,
        case_id,
        actor_id=_user_id(user),
        is_admin=user.role == Role.ADMIN,
    )


@router.post(
    "/cases/{case_id}/duplicate",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Duplikasi kasus menjadi kasus baru berstatus draf",
    responses={
        403: {"model": ErrorBody, "description": "Bukan Kontributor atau Admin."},
        404: {"model": ErrorBody, "description": "Kasus tidak ditemukan."},
        409: {
            "model": ErrorBody,
            "description": "Kode salinan baru saja dipakai salinan lain (`CASE_CODE_TAKEN`); "
            "ulangi permintaan.",
        },
        422: {
            "model": ErrorBody,
            "description": "Suite kasus asal sudah tidak aktif (`SUITE_NOT_ACTIVE`).",
        },
    },
)
def duplicate_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """PBI-4 AC2 (D8, D9): Kontributor dan Admin menyalin kasus sebagai titik awal kasus baru.

    Salinan memakai id dan case_code baru (`<kode asal>-SALIN`, lalu `-SALIN-2`, ...),
    berstatus draf, tanpa tag dev/test, dengan origin `duplicate` dan source_case_id kasus asal.
    Salinan kasus dev (atau kasus yang sudah was_dev) ikut ditandai `was_dev`. Tanpa body.
    """
    return service.duplicate_case(db, case_id, actor_id=_user_id(user))


@router.post(
    "/cases/{case_id}/submit-review",
    response_model=ReviewSubmission,
    summary="Ajukan kasus untuk direview",
    responses={
        403: {"model": ErrorBody, "description": "Bukan Author atau bukan penulis kasus ini."},
        404: _ERROR_CODES[404],
        409: {
            "model": ErrorBody,
            "description": (
                "Versi sedang ditinjau atau sudah disetujui (`VERSION_NOT_SUBMITTABLE`), "
                "atau pengajuan ulang tanpa putusan revisi pada round terakhir "
                "(`ROUND_NOT_REVISED`)."
            ),
        },
        422: {
            "model": CaseNotReadyBody,
            "description": (
                "Kelengkapan belum 100%, tag kosong, atau rujukan hukum belum lengkap "
                "(`CASE_NOT_READY`, daftar di `missing`). Suite tidak aktif "
                "(`SUITE_NOT_ACTIVE`)."
            ),
        },
    },
)
def submit_for_review(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_submit),
) -> ReviewSubmission:
    """PBI-6 AC1. The version becomes in_review and a new review round is created."""
    return service.submit_for_review(db, case_id, actor_id=_user_id(user))


@router.get(
    "/cases/{case_id}/versions",
    response_model=list[VersionSummary],
    summary="Riwayat versi satu kasus",
    responses={
        404: {"model": ErrorBody, "description": "Kasus tidak ditemukan."},
    },
)
def list_case_versions(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_read_versions),
) -> list[VersionSummary]:
    """List every stored version, oldest number first.

    Author, Reviewer, Admin, and Viewer. SCRUM-138 shows this history to
    Viewer, and a Reviewer needs it while a new version is in review.
    """
    return service.list_case_versions(db, case_id)


@router.get(
    "/cases/{case_id}/versions/compare",
    response_model=VersionCompare,
    summary="Bandingkan dua nomor versi",
    responses={
        404: {"model": ErrorBody, "description": "Kasus atau nomor versi tidak ditemukan."},
    },
)
def compare_case_versions(
    case_id: uuid.UUID,
    a: int = Query(..., ge=1, description="Nomor versi pertama"),
    b: int = Query(..., ge=1, description="Nomor versi kedua"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_read_versions),
) -> VersionCompare:
    """Diff two version numbers of one case. The diff is computed on request.

    Author, Reviewer, Admin, and Viewer. SCRUM-138 shows this compare to
    Viewer, and a Reviewer needs it while a new version is in review.
    """
    return service.compare_case_versions(db, case_id, a, b)


@router.get(
    "/cases/{case_id}/versions/current",
    response_model=CaseRead,
    summary="Versi yang sedang dikerjakan (draf, ditinjau, atau perlu revisi)",
    responses={
        403: _ERROR_CODES[403],
        404: {
            "model": ErrorBody,
            "description": (
                "Kasus tidak ditemukan, atau tidak ada versi yang sedang "
                "dikerjakan (`NO_OPEN_VERSION`)."
            ),
        },
    },
)
def get_open_version(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_write),
) -> CaseRead:
    """Return the open draft so it can be reopened after leaving the page.

    GET /cases/{id} keeps returning the approved version. Only the creator
    or an admin may read the draft.
    """
    return service.get_open_version(
        db,
        case_id,
        actor_id=_user_id(user),
        is_admin=user.role == Role.ADMIN,
    )


@router.patch(
    "/cases/{case_id}/split-tag",
    response_model=CaseRead,
    summary="Ubah tag dev/test kasus yang sudah disetujui",
    responses={
        403: {"model": ErrorBody, "description": "Bukan Admin."},
        404: _ERROR_CODES[404],
        409: {
            "model": TagConfirmationBody,
            "description": (
                "Dev ke test tanpa confirm (`TAG_CHANGE_NEEDS_CONFIRMATION`, "
                "`requires_confirmation` = true), atau kasus belum disetujui "
                "(`NO_APPROVED_VERSION`)."
            ),
        },
        422: {"model": ErrorBody, "description": "Suite tidak aktif (`SUITE_NOT_ACTIVE`)."},
    },
)
def change_split_tag(
    case_id: uuid.UUID,
    payload: SplitTagChange,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_admin),
) -> CaseRead:
    """PBI-9 AC3. Changes the approved version and any open version, and is audited.

    Moving dev to test needs confirm = true, because AI developers have
    already seen the case. The case is then marked was_dev for good.
    """
    return service.change_split_tag(
        db, case_id, payload.split_tag, actor_id=_user_id(user), confirm=payload.confirm
    )


@router.get(
    "/cases/{case_id}/completeness",
    response_model=CaseCompleteness,
    summary="Indikator kelengkapan satu kasus",
    responses={404: _ERROR_CODES[404]},
)
def get_completeness(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_can_write),
) -> CaseCompleteness:
    """Return what the case still needs before it can be sent for review."""
    return service.get_completeness(db, case_id)
