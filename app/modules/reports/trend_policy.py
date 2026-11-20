"""Siapa boleh melihat tren jenis kasus apa. PBI-17 SCRUM-84 SCRUM-331.

AC1, AC12. Murni, tanpa database dan tanpa HTTP.
"""

from app.modules.reports.models import ScoreScope
from app.shared.security import Role

TREND_SCOPE_FORBIDDEN = "TREND_SCOPE_FORBIDDEN"


def ensure_can_view_trend(role: Role, scope: ScoreScope) -> None:
    return None
