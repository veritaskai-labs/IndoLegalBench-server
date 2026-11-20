"""Siapa boleh melihat tren jenis kasus apa. PBI-17 SCRUM-84 SCRUM-331.

AC1, AC12. Murni, tanpa database dan tanpa HTTP. Tabel izin peran per jenis nilai dipakai
bersama dengan pembuat laporan (creation_policy), jadi aturan skor dev hidup di satu tempat.
"""

from app.modules.reports.creation_policy import can_use_scope
from app.modules.reports.models import ScoreScope
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role

TREND_SCOPE_FORBIDDEN = "TREND_SCOPE_FORBIDDEN"


def ensure_can_view_trend(role: Role, scope: ScoreScope) -> None:
    """Tolak (403) tren yang memuat skor dev untuk peran yang tidak berhak (AC1, AC12)."""
    if not can_use_scope(role, scope):
        raise ForbiddenError(
            "Peran Anda hanya boleh melihat tren skor test.",
            code=TREND_SCOPE_FORBIDDEN,
        )
