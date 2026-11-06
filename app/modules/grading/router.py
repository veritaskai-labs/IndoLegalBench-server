"""Endpoint HTTP modul grading: versi rubrik (PBI-14 AC3, AC7, SCRUM-222 PR B).

Di bawah /admin seperti /admin/providers: hanya Admin (keputusan PO K7). Tanpa sesi
401, peran lain 403. Viewer melihat nomor versi lewat hasil penilaian (SCRUM-227),
bukan lewat endpoint ini, jadi prompt penilai tidak pernah keluar ke peran lain.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada logika bisnis dan
tidak ada query database di file ini.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.grading import service
from app.modules.grading.schemas import (
    RubricVersionCreate,
    RubricVersionRead,
    RubricVersionSummary,
)
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

_admin_only = require_roles(Role.ADMIN)

router = APIRouter(
    prefix="/admin/rubrics",
    tags=["grading"],
    dependencies=[Depends(_admin_only)],
)

_TIDAK_ADA = {
    404: {"model": ErrorBody, "description": "Versi rubrik tidak ada (`RUBRIC_VERSION_NOT_FOUND`)."}
}
_BUAT = {
    409: {
        "model": ErrorBody,
        "description": "Versi lain baru saja disimpan (`RUBRIC_VERSION_CONFLICT`); muat ulang.",
    },
    422: {
        "model": ErrorBody,
        "description": "Isi melanggar aturan rubrik (`RUBRIC_*`, dengan `field`), "
        "atau detail Pydantic untuk batas per field.",
    },
}


@router.get("", response_model=list[RubricVersionSummary], summary="Daftar versi rubrik")
def list_versions(db: Session = Depends(get_db)) -> list[RubricVersionSummary]:
    """Semua versi, terbaru di atas. Prompt penilai hanya ada di detail."""
    return service.list_versions(db)


@router.get(
    "/{version_id}",
    response_model=RubricVersionRead,
    summary="Detail satu versi rubrik",
    responses=_TIDAK_ADA,
)
def get_version(version_id: uuid.UUID, db: Session = Depends(get_db)) -> RubricVersionRead:
    return service.get_version(db, version_id)


@router.post(
    "",
    response_model=RubricVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buat versi rubrik baru dan jadikan aktif",
    responses=_BUAT,
)
def create_version(
    payload: RubricVersionCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_admin_only),
) -> RubricVersionRead:
    """Versi baru bernomor berikutnya langsung aktif; versi lama tetap tersimpan (AC7, D7)."""
    return service.create_version(db, payload, created_by=uuid.UUID(str(user.user_id)))
