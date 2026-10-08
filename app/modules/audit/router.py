"""Endpoint HTTP modul audit.

PBI-18 Jejak audit menyeluruh.

Hanya ada endpoint baca (lapisan 2 diagram D6c). Catatan ditulis oleh
listener ORM, dan tidak ada endpoint untuk mengubah atau menghapusnya.

- GET /audit-logs: cari dan saring (AC3), Reviewer dibatasi (AC5)
- GET /audit-logs/export: unduh hasil yang sama sebagai CSV atau PDF (AC6)
- GET /audit-logs/{id}: buka kembali satu catatan (AC4)

Router hanya menerjemahkan HTTP ke pemanggilan service. Tidak ada
logika bisnis dan tidak ada query database di file ini.
"""

import uuid
from datetime import UTC, datetime

import pydantic
from fastapi import APIRouter, Depends, Query, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.modules.audit import service
from app.modules.audit.export import ExportFormat, export_filename, exporter_for
from app.modules.audit.models import AuditEntityType
from app.modules.audit.schemas import AuditLogFilter, AuditLogRead
from app.modules.auth.schemas import ErrorBody
from app.shared.database import get_db
from app.shared.pagination import Page
from app.shared.security import CurrentUser, Role, require_roles

router = APIRouter(prefix="/audit-logs", tags=["audit"])

_boleh_melihat = require_roles(Role.ADMIN, Role.REVIEWER)

_TOLAK = {
    403: {
        "model": ErrorBody,
        "description": "Peran tidak berwenang, atau Reviewer meminta kasus yang tidak ditugaskan",
    }
}


def _saringan(
    occurred_from: datetime | None = Query(
        default=None, alias="from", description="Batas awal, inklusif. Tanpa zona dianggap WIB"
    ),
    occurred_to: datetime | None = Query(
        default=None, alias="to", description="Batas akhir, inklusif. Tanpa zona dianggap WIB"
    ),
    entity_type: AuditEntityType | None = Query(default=None, description="Jenis data"),
    actor_id: uuid.UUID | None = Query(default=None),
    actor: str | None = Query(
        default=None, max_length=255, description="Sebagian nama atau email pelaku"
    ),
    case_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> AuditLogFilter:
    try:
        return AuditLogFilter(
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            entity_type=entity_type,
            actor_id=actor_id,
            actor=actor,
            case_id=case_id,
            page=page,
            size=size,
        )
    except pydantic.ValidationError as galat:
        raise RequestValidationError(galat.errors()) from None


@router.get(
    "",
    response_model=Page[AuditLogRead],
    summary="Cari dan saring catatan audit",
    responses=_TOLAK,
)
def search_logs(
    filters: AuditLogFilter = Depends(_saringan),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_boleh_melihat),
) -> Page[AuditLogRead]:
    return service.search_logs(db, filters, viewer_id=user.user_id, viewer_role=user.role)


@router.get(
    "/export",
    response_class=Response,
    summary="Unduh hasil pencarian sebagai CSV atau PDF",
    responses={
        200: {
            "content": {"text/csv": {}, "application/pdf": {}},
            "description": "File unduhan, waktu dalam WIB",
        },
        **_TOLAK,
        422: {"description": "Saringan tidak valid atau hasil terlalu besar"},
    },
)
def export_logs(
    format_: ExportFormat = Query(alias="format"),
    filters: AuditLogFilter = Depends(_saringan),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_boleh_melihat),
) -> Response:
    rows = service.list_for_export(db, filters, viewer_id=user.user_id, viewer_role=user.role)
    exporter = exporter_for(format_)
    sekarang = datetime.now(UTC)
    return Response(
        content=exporter.render(rows, generated_at=sekarang),
        media_type=exporter.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename(format_, sekarang)}"'
        },
    )


@router.get(
    "/{log_id}",
    response_model=AuditLogRead,
    summary="Buka kembali satu catatan audit",
    responses={**_TOLAK, 404: {"model": ErrorBody, "description": "Catatan tidak ditemukan"}},
)
def get_log(
    log_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_boleh_melihat),
) -> AuditLogRead:
    return service.get_log(db, log_id, viewer_id=user.user_id, viewer_role=user.role)
