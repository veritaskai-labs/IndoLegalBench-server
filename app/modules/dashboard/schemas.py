"""Bentuk response modul dashboard. PBI-19, SCRUM-154."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.reviews.schemas import PendingReview


class CaseCounts(BaseModel):
    """Jumlah kasus per status versi yang berlaku, sama seperti daftar kasus."""

    draft: int
    in_review: int
    needs_revision: int
    approved: int


class OwnCaseSummary(BaseModel):
    """Kasus yang ditulis pengguna yang login, per status versi yang berlaku (PBI-24 AC6)."""

    case_counts: CaseCounts
    total_cases: int


class LatestRunSummary(BaseModel):
    """Sesi pengukuran terakhir. Selalu null sampai PBI-11 mengisinya (AC1)."""


class DashboardSummary(BaseModel):
    """GET /dashboard: ringkasan kerja pengguna yang login, sesuai perannya."""

    generated_at: datetime = Field(description="Waktu data dihitung (AC2)")
    case_counts: CaseCounts
    total_cases: int
    waiting_for_review: int = Field(
        description=(
            "Kasus in_review yang round berjalannya belum punya verdict sah dari kedua slot "
            "reviewer, termasuk kasus dengan 0 atau 1 reviewer. Bisa lebih besar dari total "
            "antrean reviewer."
        )
    )
    own_cases: OwnCaseSummary | None = Field(
        description=(
            "Hanya Kontributor: kasus yang ia tulis per status versi yang berlaku, berisi nol "
            "bila belum ada (PBI-24 AC6). null untuk peran lain. case_counts dan total_cases "
            "di atas tetap menghitung semua kasus."
        )
    )
    review_queue: list[PendingReview] | None = Field(
        description=(
            "Hanya Reviewer: kasus yang ditugaskan dan belum dinilai, terlama dulu (AC6). "
            "null untuk peran lain."
        )
    )
    needs_reassignment: int | None = Field(
        description="Hanya Admin: kasus yang perlu penugasan ulang reviewer. null untuk peran lain."
    )
    latest_run: LatestRunSummary | None = Field(
        default=None, description="Sesi pengukuran terakhir. null sampai PBI-11 ada."
    )
