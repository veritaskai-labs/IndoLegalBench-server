"""Query database modul legal_refs. Satu-satunya tempat yang membaca tabelnya."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.legal_refs.models import RegulationType


def regulation_types(db: Session, *, include_inactive: bool) -> Sequence[RegulationType]:
    """Jenis peraturan menurut urutan seed. Yang nonaktif hanya kalau diminta."""
    query = select(RegulationType).order_by(RegulationType.urutan)
    if not include_inactive:
        query = query.where(RegulationType.is_active.is_(True))
    return db.scalars(query).all()
