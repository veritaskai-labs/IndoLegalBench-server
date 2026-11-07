"""Aturan siapa boleh melihat isi kasus sesi pengukuran. PBI-11 AC9, SCRUM-261."""

from app.shared.security import Role


def can_view_case_content(role: Role, split_tag: str | None) -> bool:
    return False
