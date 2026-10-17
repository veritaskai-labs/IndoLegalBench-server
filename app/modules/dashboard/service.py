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
from app.modules.dashboard.schemas import CaseCounts, DashboardSummary
from app.modules.reviews import service as reviews_service
from app.shared.security import Role


def summary(db: Session, *, user_id: uuid.UUID, role: Role) -> DashboardSummary:
    """Ringkasan untuk Author, Reviewer, atau Admin. Router sudah menolak Viewer."""
    counts = cases_service.count_by_status(db)
    return DashboardSummary(
        generated_at=datetime.now(UTC),
        case_counts=CaseCounts(**{status.value: count for status, count in counts.items()}),
        total_cases=sum(counts.values()),
        waiting_for_review=reviews_service.count_cases_waiting(db),
        review_queue=(
            reviews_service.pending_queue(db, user_id) if role == Role.REVIEWER else None
        ),
        needs_reassignment=(
            reviews_service.count_cases_needing_reassignment(db) if role == Role.ADMIN else None
        ),
        latest_run=None,
    )
