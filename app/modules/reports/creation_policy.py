"""Siapa boleh membuat laporan jenis apa. PBI-15 SCRUM-79 SCRUM-313.

AC7, AC9, AC14. Murni, tanpa database dan tanpa HTTP.
"""

from app.modules.reports.models import ProductLabelMode, ScoreScope
from app.shared.exceptions import ForbiddenError
from app.shared.security import Role

REPORT_SCOPE_FORBIDDEN = "REPORT_SCOPE_FORBIDDEN"
REPORT_LABEL_MODE_FORBIDDEN = "REPORT_LABEL_MODE_FORBIDDEN"

# Tabel izin, tertutup secara bawaan: peran yang tidak tercatat tidak boleh apa-apa.
# Skor dev hanya untuk Admin dan Developer AI (AC14); Viewer dan Kontributor hanya Test.
_SCOPES_BY_ROLE: dict[Role, frozenset[ScoreScope]] = {
    Role.ADMIN: frozenset(ScoreScope),
    Role.DEVELOPER_AI: frozenset(ScoreScope),
    Role.VIEWER: frozenset({ScoreScope.TEST}),
    Role.CONTRIBUTOR: frozenset({ScoreScope.TEST}),
}

# Hanya Admin yang memutuskan label produk (AC9); yang lain memakai nama asli.
_LABEL_MODES_BY_ROLE: dict[Role, frozenset[ProductLabelMode]] = {
    Role.ADMIN: frozenset(ProductLabelMode),
    Role.DEVELOPER_AI: frozenset({ProductLabelMode.REAL_NAME}),
    Role.VIEWER: frozenset({ProductLabelMode.REAL_NAME}),
    Role.CONTRIBUTOR: frozenset({ProductLabelMode.REAL_NAME}),
}


def ensure_can_create(role: Role, scope: ScoreScope, label_mode: ProductLabelMode) -> None:
    """Tolak (403) permintaan yang melampaui hak peran. Jenis nilai diperiksa lebih dulu."""
    if scope not in _SCOPES_BY_ROLE.get(role, frozenset()):
        raise ForbiddenError(
            "Peran Anda tidak boleh membuat laporan dengan jenis nilai ini.",
            code=REPORT_SCOPE_FORBIDDEN,
        )
    if label_mode not in _LABEL_MODES_BY_ROLE.get(role, frozenset()):
        raise ForbiddenError(
            "Hanya Admin yang boleh memakai kode sebagai label produk.",
            code=REPORT_LABEL_MODE_FORBIDDEN,
        )
