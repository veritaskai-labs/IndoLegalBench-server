"""Aturan siapa boleh melihat isi kasus sesi pengukuran. PBI-11 AC9, SCRUM-261.

Satu-satunya tempat yang memutuskan penyamaran, dipakai detail sesi dan detail
teknis percobaan. Fungsi murni: tanpa database, tanpa HTTP.

Admin melihat semua kasus. Developer AI hanya melihat kasus dev (SCRUM-183).
Aturannya fail-closed: peran yang tidak terdaftar, tag kosong, dan tag yang
tidak dikenal dianggap tertutup, bukan terbuka.
"""

from collections.abc import Mapping

from app.shared.security import Role

# None berarti semua split terbuka. Peran baru tertutup sampai didaftarkan di sini.
_VISIBLE_SPLITS: Mapping[Role, frozenset[str] | None] = {
    Role.ADMIN: None,
    Role.DEVELOPER_AI: frozenset({"dev"}),
}


def can_view_case_content(role: Role, split_tag: str | None) -> bool:
    """True bila peran ini boleh melihat pertanyaan, jawaban, dan detail teknis kasus."""
    if role not in _VISIBLE_SPLITS:
        return False
    visible = _VISIBLE_SPLITS[role]
    return visible is None or split_tag in visible
