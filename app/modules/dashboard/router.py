"""Endpoint HTTP modul dashboard. PBI-19, SCRUM-154.

Router hanya menerjemahkan HTTP ke pemanggilan service.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.modules.auth.schemas import ErrorBody
from app.modules.dashboard import service
from app.modules.dashboard.schemas import DashboardSummary
from app.shared.database import get_db
from app.shared.security import REVIEWER_ROLES, WRITER_ROLES, CurrentUser, Role, require_roles

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# PBI-19 hanya untuk Kontributor (dulu Author dan Reviewer) dan Admin. Viewer diarahkan FE ke
# halaman laporan setelah login (PBI-1 AC1), jadi di sini 403.
_punya_dashboard = require_roles(*WRITER_ROLES, *REVIEWER_ROLES, Role.ADMIN)


@router.get(
    "",
    response_model=DashboardSummary,
    summary="Ringkasan dashboard sesuai peran",
    responses={403: {"model": ErrorBody, "description": "Viewer tidak punya dashboard."}},
)
def get_dashboard(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_punya_dashboard),
) -> DashboardSummary:
    """PBI-19 AC1, AC2, AC6. Safe to poll every 60 s."""
    user_id = user.user_id if isinstance(user.user_id, uuid.UUID) else uuid.UUID(str(user.user_id))
    return service.summary(db, user_id=user_id, role=user.role)
