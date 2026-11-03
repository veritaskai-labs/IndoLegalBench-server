"""Logika bisnis modul dashboard. PBI-19, SCRUM-154.

Modul ini tidak punya tabel. Semua angka dibaca lewat service milik
cases dan reviews, sesuai aturan bahwa modul tidak membaca repository
modul lain. Setiap angka adalah satu query agregat, jadi aman di-poll
tiap 60 detik (AC2).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.modules.cases import service as cases_service
from app.modules.dashboard.schemas import (
    CaseCounts,
    DashboardSummary,
    LatestRunSummary,
    OwnCaseSummary,
)
from app.modules.reviews import service as reviews_service
from app.modules.runs import service as runs_service
from app.shared.security import REVIEWER_ROLES, WRITER_ROLES, Role


def summary(db: Session, *, user_id: uuid.UUID, role: Role) -> DashboardSummary:
    """Ringkasan untuk Kontributor atau Admin. Router sudah menolak Viewer."""
    counts = cases_service.count_by_status(db)
    return DashboardSummary(
        generated_at=datetime.now(UTC),
        case_counts=_case_counts(counts),
        total_cases=sum(counts.values()),
        own_cases=_own_cases(db, user_id) if role in WRITER_ROLES else None,
        waiting_for_review=reviews_service.count_cases_waiting(db),
        review_queue=(
            reviews_service.pending_queue(db, user_id) if role in REVIEWER_ROLES else None
        ),
        needs_reassignment=(
            reviews_service.count_cases_needing_reassignment(db) if role == Role.ADMIN else None
        ),
        latest_run=_latest_run(db) if role == Role.ADMIN else None,
    )


def _case_counts(counts: dict) -> CaseCounts:
    return CaseCounts(**{status.value: count for status, count in counts.items()})


def _own_cases(db: Session, user_id: uuid.UUID) -> OwnCaseSummary:
    """Pekerjaan menulis Kontributor (PBI-24 AC6), dengan aturan versi berlaku yang sama."""
    milik = cases_service.count_by_status(db, created_by=user_id)
    return OwnCaseSummary(case_counts=_case_counts(milik), total_cases=sum(milik.values()))


def _latest_run(db: Session) -> LatestRunSummary | None:
    """Data sesi hanya untuk Admin dan Developer AI, dan Developer AI tidak punya dashboard."""
    terakhir = runs_service.latest_session(db)
    return None if terakhir is None else LatestRunSummary(**terakhir.model_dump())
