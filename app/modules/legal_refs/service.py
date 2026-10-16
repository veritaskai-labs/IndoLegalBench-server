"""Aturan bisnis modul legal_refs (PBI-21, SCRUM-160).

Modul lain memakai fungsi di sini, bukan tabel legal_refs secara langsung.
"""

from sqlalchemy.orm import Session

from app.modules.legal_refs import repository
from app.modules.legal_refs.schemas import RegulationTypeRead


def list_regulation_types(
    db: Session, *, include_inactive: bool = False
) -> list[RegulationTypeRead]:
    """Daftar untuk dropdown editor (AC2)."""
    return [
        RegulationTypeRead.model_validate(baris)
        for baris in repository.regulation_types(db, include_inactive=include_inactive)
    ]
