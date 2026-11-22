"""Siapa boleh membuat laporan jenis apa. PBI-15 SCRUM-79 SCRUM-313.

AC7, AC9, AC14. Murni, tanpa database dan tanpa HTTP.
"""

from collections.abc import Collection

from app.modules.cases.models import SplitTag
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


# Kode kasus hanya untuk peran yang boleh membaca kasus (sama dengan modul cases): Viewer
# tidak bisa membaca kasus dan Developer AI hanya melihat kasus test yang di-mask.
_ROLES_THAT_SEE_CASE_CODES = frozenset({Role.ADMIN, Role.CONTRIBUTOR})


def can_see_case_codes(role: Role) -> bool:
    """Boleh tidaknya peran melihat kode kasus, misalnya di alasan penolakan 409."""
    return role in _ROLES_THAT_SEE_CASE_CODES


def can_use_scope(role: Role, scope: ScoreScope) -> bool:
    """Boleh tidaknya peran memakai satu jenis nilai; dipakai pembuat laporan dan tren."""
    return scope in _SCOPES_BY_ROLE.get(role, frozenset())


# Jenis nilai yang masuk akal untuk jenis kasus pada irisan (AC14): Dev + Test hanya bila
# keduanya ada. Urutannya tetap, supaya respons dan test tidak berubah-ubah.
_SCOPES_BY_KINDS: dict[frozenset[SplitTag], tuple[ScoreScope, ...]] = {
    frozenset({SplitTag.TEST}): (ScoreScope.TEST,),
    frozenset({SplitTag.DEV}): (ScoreScope.DEV,),
    frozenset({SplitTag.DEV, SplitTag.TEST}): (
        ScoreScope.TEST,
        ScoreScope.DEV,
        ScoreScope.DEV_TEST,
    ),
}


def available_scopes(role: Role, case_kinds: Collection[SplitTag]) -> tuple[ScoreScope, ...]:
    """Jenis nilai yang boleh dipilih peran ini untuk irisan kasus dengan jenis tertentu.

    Irisan kosong tidak menawarkan apa-apa. Peran dibatasi tabel izin yang sama dengan
    ensure_can_create, jadi tombol yang tampil tidak pernah berbeda dari yang diterima POST.
    """
    offered = _SCOPES_BY_KINDS.get(frozenset(case_kinds), ())
    return tuple(scope for scope in offered if can_use_scope(role, scope))


def ensure_can_create(role: Role, scope: ScoreScope, label_mode: ProductLabelMode) -> None:
    """Tolak (403) permintaan yang melampaui hak peran. Jenis nilai diperiksa lebih dulu."""
    if not can_use_scope(role, scope):
        raise ForbiddenError(
            "Peran Anda tidak boleh membuat laporan dengan jenis nilai ini.",
            code=REPORT_SCOPE_FORBIDDEN,
        )
    if label_mode not in _LABEL_MODES_BY_ROLE.get(role, frozenset()):
        raise ForbiddenError(
            "Hanya Admin yang boleh memakai kode sebagai label produk.",
            code=REPORT_LABEL_MODE_FORBIDDEN,
        )
