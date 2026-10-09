"""Endpoint HTTP modul providers.

PBI-10 Registri produk AI yang akan diukur.

Path choice for SCRUM-115: Jira writes these routes as `/admin/ai-products`.
The module stub and the PBI-1 role matrix in `app/modules/auth/README.md`
used to say `/providers`. This router ships `/admin/providers`: the `/admin`
prefix matches `/admin/users` and the admin-only rule, and `providers` stays
the resource name of this module.

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.providers import service
from app.modules.providers.schemas import (
    AiProductCreate,
    AiProductRead,
    AiProductUpdate,
    ConnectionTestRead,
)
from app.shared.database import get_db
from app.shared.security import CurrentUser, Role, require_roles

_admin_only = require_roles(Role.ADMIN)

router = APIRouter(
    prefix="/admin/providers",
    tags=["providers"],
    dependencies=[Depends(_admin_only)],
)

_KONFLIK = {
    409: {
        "model": ErrorBody,
        "description": "Nama produk sudah dipakai (`AI_PRODUCT_NAME_TAKEN`).",
    }
}
_TIDAK_ADA = {
    404: {"model": ErrorBody, "description": "Produk AI tidak ditemukan."},
}


def _user_id(user: CurrentUser) -> uuid.UUID:
    if isinstance(user.user_id, uuid.UUID):
        return user.user_id
    return uuid.UUID(str(user.user_id))


@router.post(
    "",
    response_model=AiProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Daftarkan produk AI",
    responses=_KONFLIK,
)
def create_product(
    payload: AiProductCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_admin_only),
) -> AiProductRead:
    return service.create_product(db, payload, created_by=_user_id(user))


@router.get("", response_model=list[AiProductRead], summary="Daftar produk AI")
def list_products(
    is_active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[AiProductRead]:
    return service.list_products(db, is_active=is_active)


@router.get(
    "/{product_id}",
    response_model=AiProductRead,
    summary="Detail satu produk AI",
    responses=_TIDAK_ADA,
)
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db)) -> AiProductRead:
    return service.get_product(db, product_id)


@router.patch(
    "/{product_id}",
    response_model=AiProductRead,
    summary="Ubah produk AI",
    responses=_KONFLIK | _TIDAK_ADA,
)
def update_product(
    product_id: uuid.UUID,
    payload: AiProductUpdate,
    db: Session = Depends(get_db),
) -> AiProductRead:
    return service.update_product(db, product_id, payload)


@router.post(
    "/{product_id}/deactivate",
    response_model=AiProductRead,
    summary="Nonaktifkan produk AI",
    responses=_TIDAK_ADA,
)
def deactivate_product(product_id: uuid.UUID, db: Session = Depends(get_db)) -> AiProductRead:
    return service.set_active(db, product_id, is_active=False)


@router.post(
    "/{product_id}/activate",
    response_model=AiProductRead,
    summary="Aktifkan produk AI",
    responses=_TIDAK_ADA,
)
def activate_product(product_id: uuid.UUID, db: Session = Depends(get_db)) -> AiProductRead:
    return service.set_active(db, product_id, is_active=True)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus produk AI (soft delete)",
    responses=_TIDAK_ADA,
)
def delete_product(product_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    service.delete_product(db, product_id)


@router.post(
    "/{product_id}/test-connection",
    response_model=ConnectionTestRead,
    response_model_exclude_none=True,
    summary="Uji koneksi produk AI",
    responses=_TIDAK_ADA,
)
def test_connection(product_id: uuid.UUID, db: Session = Depends(get_db)) -> ConnectionTestRead:
    return service.test_connection(db, product_id)
