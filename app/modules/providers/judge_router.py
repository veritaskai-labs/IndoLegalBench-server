"""Endpoint HTTP provider penilai (LLM judge). PBI-14 AC6, SCRUM-258.

Path (keputusan PO 7 Nov): Jira menulis `/judge-providers`. Registri lengkap ada di
`/admin/judge-providers` (Admin), seperti `/admin/providers`. Daftar pilihan penilai
aktif untuk form pengukuran ada di `/judge-providers`, seperti `/ai-products`, karena
Developer AI juga membuat pengukuran (klien No. 35) dan perlu membacanya.

Router hanya menerjemahkan HTTP ke pemanggilan service.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.providers import judge_service
from app.modules.providers.judge_schemas import (
    JudgeProviderCreate,
    JudgeProviderOption,
    JudgeProviderRead,
    JudgeProviderUpdate,
)
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

_admin_only = require_roles(Role.ADMIN)

router = APIRouter(
    prefix="/admin/judge-providers",
    tags=["judge-providers"],
    dependencies=[Depends(_admin_only)],
)
options_router = APIRouter(prefix="/judge-providers", tags=["judge-providers"])

_KONFLIK = {
    409: {"model": ErrorBody, "description": "Nama sudah dipakai (`JUDGE_PROVIDER_NAME_TAKEN`)."}
}
_TIDAK_ADA = {
    404: {
        "model": ErrorBody,
        "description": "Penilai tidak ditemukan (`JUDGE_PROVIDER_NOT_FOUND`).",
    }
}


@router.post(
    "",
    response_model=JudgeProviderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Daftarkan provider penilai",
    responses=_KONFLIK,
)
def create_judge(
    payload: JudgeProviderCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_admin_only),
) -> JudgeProviderRead:
    return judge_service.create_judge(db, payload, created_by=user.user_id)


@router.get(
    "",
    response_model=list[JudgeProviderRead],
    summary="Daftar provider penilai",
    description="Semua penilai termasuk yang nonaktif; yang aktif lebih dulu, lalu urut nama.",
)
def list_judges(db: Session = Depends(get_db)) -> list[JudgeProviderRead]:
    return judge_service.list_judges(db)


@router.get(
    "/{judge_id}",
    response_model=JudgeProviderRead,
    summary="Detail provider penilai",
    responses=_TIDAK_ADA,
)
def get_judge(judge_id: uuid.UUID, db: Session = Depends(get_db)) -> JudgeProviderRead:
    return judge_service.get_judge(db, judge_id)


@router.patch(
    "/{judge_id}",
    response_model=JudgeProviderRead,
    summary="Ganti kredensial, harga, atau anggaran penilai",
    description=(
        "Hanya credential, harga, dan monthly_budget_idr. Nama, jenis, URL, dan model "
        "tidak bisa diganti karena dirujuk sesi lama; null eksplisit ditolak."
    ),
    responses=_TIDAK_ADA,
)
def update_judge(
    judge_id: uuid.UUID, payload: JudgeProviderUpdate, db: Session = Depends(get_db)
) -> JudgeProviderRead:
    return judge_service.update_judge(db, judge_id, payload)


@router.post(
    "/{judge_id}/deactivate",
    response_model=JudgeProviderRead,
    summary="Nonaktifkan provider penilai",
    responses=_TIDAK_ADA,
)
def deactivate_judge(judge_id: uuid.UUID, db: Session = Depends(get_db)) -> JudgeProviderRead:
    return judge_service.set_active(db, judge_id, is_active=False)


@router.post(
    "/{judge_id}/activate",
    response_model=JudgeProviderRead,
    summary="Aktifkan provider penilai",
    responses=_TIDAK_ADA,
)
def activate_judge(judge_id: uuid.UUID, db: Session = Depends(get_db)) -> JudgeProviderRead:
    return judge_service.set_active(db, judge_id, is_active=True)


@options_router.get(
    "",
    response_model=list[JudgeProviderOption],
    summary="Daftar pilihan provider penilai (produk AI otomatis aktif)",
    description=(
        "Untuk dropdown penilai di form pengukuran (Admin dan Developer AI). Isinya produk AI "
        "otomatis yang aktif dan belum dihapus, sama dengan halaman Provider. Hanya id, nama, "
        "jenis, dan model; tanpa URL, hint kredensial, harga, atau anggaran."
    ),
    responses={403: {"model": ErrorBody, "description": "Peran tidak berwenang."}},
    dependencies=[Depends(require_roles(Role.ADMIN, Role.DEVELOPER_AI))],
)
def list_judge_options(db: Session = Depends(get_db)) -> list[JudgeProviderOption]:
    return judge_service.list_options(db)
