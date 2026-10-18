"""Endpoint HTTP modul legal_refs (PBI-21, SCRUM-160).

Router hanya menerjemahkan HTTP ke pemanggilan service.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.cases import service as cases_service
from app.modules.cases.schemas import LegalRefPreviewRequest, LegalRefPreviewResponse
from app.modules.legal_refs import service
from app.modules.legal_refs.schemas import RegulationTypeRead
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(tags=["legal-refs"])

# Semua peran membaca rujukan (editor, review, riwayat), jadi semua butuh labelnya.
_semua_peran = require_roles(Role.AUTHOR, Role.REVIEWER, Role.ADMIN, Role.VIEWER)
# Pratinjau hanya untuk yang boleh menulis kasus (SCRUM-162).
_penulis = require_roles(Role.AUTHOR, Role.ADMIN)


@router.get(
    "/regulation-types",
    response_model=list[RegulationTypeRead],
    summary="Daftar jenis peraturan (JDIH BPK) untuk rujukan hukum",
    responses={
        401: {"model": ErrorBody, "description": "Belum login."},
        403: {"model": ErrorBody, "description": "Peran tidak dikenal."},
    },
)
def list_regulation_types(
    include_inactive: bool = Query(
        default=False,
        description="Sertakan jenis nonaktif, untuk label rujukan lama yang masih memakainya.",
    ),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(_semua_peran),
) -> list[RegulationTypeRead]:
    """Return the regulation types ordered by urutan, active ones unless asked otherwise."""
    return service.list_regulation_types(db, include_inactive=include_inactive)


@router.post(
    "/legal-refs/preview",
    response_model=LegalRefPreviewResponse,
    summary="Pratinjau rujukan hukum saat mengetik",
    description=(
        "Satu item per rujukan: display_text kalau rujukan itu akan diterima saat simpan, "
        "atau error pertamanya dengan code dan field yang sama seperti simpan. "
        "Rujukan yang salah tidak membuat request ini 422. Tidak menulis apa pun."
    ),
    responses={
        401: {"model": ErrorBody, "description": "Belum login."},
        403: {
            "model": ErrorBody,
            "description": "Bukan Author/Admin, atau case_id milik orang lain.",
        },
        404: {"model": ErrorBody, "description": "case_id tidak ditemukan."},
        422: {"model": ErrorBody, "description": "Body bukan daftar rujukan, atau lebih dari 100."},
    },
)
def preview_legal_refs(
    payload: LegalRefPreviewRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_penulis),
) -> LegalRefPreviewResponse:
    """Preview each citation with the save rules (PBI-21 AC6, AC8, AC10)."""
    items = cases_service.preview_legal_refs(
        db,
        payload.legal_refs,
        case_id=payload.case_id,
        actor_id=uuid.UUID(str(user.user_id)),
        is_admin=user.role == Role.ADMIN,
    )
    return LegalRefPreviewResponse(items=items)
