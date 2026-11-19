"""Siapa boleh membaca laporan tersimpan (PBI-15 SCRUM-320, matriks peran D9).

Hak satu pengguna diterjemahkan sekali menjadi ReportVisibility. Objek yang sama dipakai
repository untuk menyaring daftar (GET /reports) dan service untuk membuka satu laporan
(GET /reports/{id}, PDF), jadi daftar dan detail tidak mungkin berbeda aturan.

Aturan D9:
- Laporan tersimpan (saved) hanya untuk pembuatnya dan Admin (keputusan SA).
- Laporan terbit dan versi lamanya (superseded) untuk semua pengguna login.
- Laporan berisi skor dev hanya untuk Admin dan Developer AI, walau milik sendiri.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.modules.reports.models import ReportStatus
from app.shared.security import CurrentUser, Role

DEV_SCORE_ROLES = frozenset({Role.ADMIN, Role.DEVELOPER_AI})


class _ReadableReport(Protocol):
    status: ReportStatus
    created_by: uuid.UUID
    has_dev_scores: bool


@dataclass(frozen=True)
class ReportVisibility:
    viewer_id: uuid.UUID
    # Admin membuka laporan tersimpan milik siapa pun, misalnya untuk menerbitkannya.
    all_saved: bool
    include_dev: bool

    def allows(self, report: _ReadableReport) -> bool:
        if report.has_dev_scores and not self.include_dev:
            return False
        if self.all_saved or report.status != ReportStatus.SAVED:
            return True
        return report.created_by == self.viewer_id


def visibility_for(user: CurrentUser) -> ReportVisibility:
    return ReportVisibility(
        viewer_id=user.user_id,
        all_saved=user.role is Role.ADMIN,
        include_dev=user.role in DEV_SCORE_ROLES,
    )
