"""Endpoint HTTP modul reviews. PBI-6, SCRUM-145.

Router hanya menerjemahkan HTTP ke pemanggilan service. Aturan blind,
keputusan round, dan semua pengecekan ada di service.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.reviews import service
from app.modules.reviews.schemas import AssignedReview, CaseReview, VerdictResult, VerdictWrite
from app.modules.reviews.visibility import Viewer
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(tags=["reviews"])

# D5a: hanya Reviewer yang menilai dan punya dashboard "Perlu Direview".
_reviewer = require_roles(Role.REVIEWER)
# D5a: Author kasus sendiri, Reviewer yang ditugaskan, dan Admin. Dicek lagi di service.
_can_read_review = require_roles(Role.AUTHOR, Role.REVIEWER, Role.ADMIN)


def _user_id(user: CurrentUser) -> uuid.UUID:
    """Session user id as UUID. The dependency may hand back a string."""
    return user.user_id if isinstance(user.user_id, uuid.UUID) else uuid.UUID(str(user.user_id))


@router.get(
    "/reviews/assigned",
    response_model=list[AssignedReview],
    summary="Kasus yang ditugaskan ke reviewer yang login",
)
def list_assigned(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_reviewer),
) -> list[AssignedReview]:
    """Active assignments in open rounds, with the case body and only your own verdict."""
    return service.list_assigned(db, _user_id(user))


@router.post(
    "/reviews/{assignment_id}/verdict",
    response_model=VerdictResult,
    status_code=status.HTTP_201_CREATED,
    summary="Kirim penilaian review",
    responses={
        403: {
            "model": ErrorBody,
            "description": (
                "Penugasan milik reviewer lain (`NOT_YOUR_ASSIGNMENT`) atau reviewer "
                "adalah penulis kasus (`REVIEWER_IS_AUTHOR`)."
            ),
        },
        404: {"model": ErrorBody, "description": "Penugasan tidak ditemukan."},
        409: {
            "model": ErrorBody,
            "description": (
                "Penugasan sudah replaced/void (`ASSIGNMENT_NOT_ACTIVE`), round tidak "
                "open (`ROUND_NOT_OPEN`), atau penilaian sudah dikirim "
                "(`VERDICT_ALREADY_SUBMITTED`)."
            ),
        },
        422: {
            "model": ErrorBody,
            "description": "Komentar kosong untuk decision = revise (`COMMENT_REQUIRED`).",
        },
    },
)
def submit_verdict(
    assignment_id: uuid.UUID,
    payload: VerdictWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_reviewer),
) -> VerdictResult:
    """PBI-6 AC2-AC6. The response carries your own verdict only.

    When this is the last verdict of the round, the round is decided in the
    same transaction and the case version becomes approved or needs_revision.
    """
    return service.submit_verdict(
        db,
        assignment_id,
        reviewer_id=_user_id(user),
        decision=payload.decision,
        comment=payload.comment,
    )


@router.get(
    "/cases/{case_id}/review",
    response_model=CaseReview,
    summary="Review terakhir satu kasus",
    responses={
        403: {"model": ErrorBody, "description": "Tidak terlibat di kasus ini."},
        404: {
            "model": ErrorBody,
            "description": "Kasus tidak ditemukan, atau belum pernah diajukan (`NO_REVIEW_ROUND`).",
        },
    },
)
def get_case_review(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_can_read_review),
) -> CaseReview:
    """PBI-6 AC3. While the round is open, another reviewer's verdict is null.

    Admin sees every verdict at any time (client decision in SCRUM-145).
    """
    return service.get_case_review(db, case_id, Viewer(_user_id(user), Role(user.role)))
