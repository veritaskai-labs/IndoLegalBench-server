"""Siapa boleh membuat laporan jenis apa. PBI-15 SCRUM-79 SCRUM-313.

AC7, AC9, AC14. Murni, tanpa database dan tanpa HTTP.
"""

from app.modules.reports.models import ProductLabelMode, ScoreScope
from app.shared.security import Role

REPORT_SCOPE_FORBIDDEN = "REPORT_SCOPE_FORBIDDEN"
REPORT_LABEL_MODE_FORBIDDEN = "REPORT_LABEL_MODE_FORBIDDEN"


def ensure_can_create(role: Role, scope: ScoreScope, label_mode: ProductLabelMode) -> None:
    raise NotImplementedError
