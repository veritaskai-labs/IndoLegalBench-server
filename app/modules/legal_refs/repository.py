"""Query database modul legal_refs. Satu-satunya tempat yang membaca tabelnya."""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.legal_refs.models import LegalRef, LegalRefLink, RegulationType


def regulation_types(db: Session, *, include_inactive: bool) -> Sequence[RegulationType]:
    """Jenis peraturan menurut urutan seed. Yang nonaktif hanya kalau diminta."""
    query = select(RegulationType).order_by(RegulationType.urutan)
    if not include_inactive:
        query = query.where(RegulationType.is_active.is_(True))
    return db.scalars(query).all()


def delete_for_version(db: Session, version_id: uuid.UUID) -> None:
    """Hapus rujukan satu versi beserta link-nya. Tidak commit.

    Link dihapus dulu, karena SQLite di test tidak menjalankan ON DELETE CASCADE.
    """
    rujukan = select(LegalRef.id).where(LegalRef.case_version_id == version_id)
    db.execute(delete(LegalRefLink).where(LegalRefLink.legal_ref_id.in_(rujukan)))
    db.execute(delete(LegalRef).where(LegalRef.case_version_id == version_id))


def add(db: Session, rujukan: list[LegalRef]) -> None:
    """Tambahkan rujukan baru ke session. Tidak commit."""
    db.add_all(rujukan)
