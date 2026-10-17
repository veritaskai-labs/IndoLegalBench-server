"""Endpoint HTTP modul legal_refs (PBI-21, SCRUM-160).

Router hanya menerjemahkan HTTP ke pemanggilan service.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.legal_refs import service
from app.modules.legal_refs.schemas import RegulationTypeRead
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(tags=["legal-refs"])

# Semua peran membaca rujukan (editor, review, riwayat), jadi semua butuh labelnya.
_semua_peran = require_roles(Role.AUTHOR, Role.REVIEWER, Role.ADMIN, Role.VIEWER)


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
