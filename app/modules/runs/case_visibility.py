"""Aturan siapa boleh melihat isi kasus sesi pengukuran. PBI-11 AC9, SCRUM-261.

Satu-satunya tempat yang memutuskan penyamaran, dipakai detail sesi dan detail
teknis percobaan. Fungsi murni: tanpa database, tanpa HTTP.

Admin melihat semua kasus. Developer AI hanya melihat kasus dev (SCRUM-183).
Aturannya fail-closed: peran yang tidak terdaftar, tag kosong, dan tag yang
tidak dikenal dianggap tertutup, bukan terbuka.

PBI-16 SCRUM-326 menambah tabel kedua untuk penelusuran angka laporan (D6 langkah 6,
D9 Sprint 4). Tabelnya terpisah supaya membuka Kontributor di penelusuran tidak ikut
membuka isi kasus di halaman sesi pengukuran.
"""

from collections.abc import Mapping

from app.shared.security import Role

# None berarti semua split terbuka. Peran baru tertutup sampai didaftarkan di sini.
_VISIBLE_SPLITS: Mapping[Role, frozenset[str] | None] = {
    Role.ADMIN: None,
    Role.DEVELOPER_AI: frozenset({"dev"}),
}


# D9 Sprint 4, baris "Penelusuran: isi kasus test/dev, jawaban asli, alasan":
# Kontributor hanya kasus test, Developer AI hanya kasus dev, Viewer tidak sama sekali.
_DRILLDOWN_SPLITS: Mapping[Role, frozenset[str] | None] = {
    Role.ADMIN: None,
    Role.CONTRIBUTOR: frozenset({"test"}),
    Role.DEVELOPER_AI: frozenset({"dev"}),
}


def can_view_case_content(role: Role, split_tag: str | None) -> bool:
    """True bila peran ini boleh melihat pertanyaan, jawaban, dan detail teknis kasus."""
    return _allows(_VISIBLE_SPLITS, role, split_tag)


def can_view_drilldown_content(role: Role, split_tag: str | None) -> bool:
    """True bila peran ini boleh membuka isi kasus, jawaban asli, dan alasan penilaian
    dari penelusuran angka laporan (PBI-16 AC3)."""
    return _allows(_DRILLDOWN_SPLITS, role, split_tag)


def _allows(table: Mapping[Role, frozenset[str] | None], role: Role, split_tag: str | None) -> bool:
    if role not in table:
        return False
    visible = table[role]
    return visible is None or split_tag in visible
