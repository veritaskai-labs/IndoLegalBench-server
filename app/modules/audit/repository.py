"""Akses database modul audit.

ATURAN: hanya audit/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis. Tabel audit_logs
append-only: tidak ada fungsi ubah, dan satu-satunya penghapusan adalah
retensi, yang hanya lolos trigger lewat role ilb_retention.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


@dataclass(frozen=True)
class SearchCriteria:
    """Saringan yang sudah diolah service. None berarti tidak disaring."""

    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    entity_type: str | None = None
    actor_ids: tuple[uuid.UUID, ...] | None = None
    case_id: uuid.UUID | None = None
    # Cakupan kasus Reviewer (AC5). Himpunan kosong berarti tidak ada baris.
    case_scope: frozenset[uuid.UUID] | None = None


def add(db: Session, row: AuditLog) -> AuditLog:
    """Sengaja tanpa commit. Baris ikut tersimpan saat perubahannya di-commit."""
    db.add(row)
    return row


def delete_older_than(db: Session, cutoff: datetime) -> int:
    """Hapus catatan dengan occurred_at sebelum cutoff. Tanpa commit."""
    result = db.execute(delete(AuditLog).where(AuditLog.occurred_at < cutoff))
    return result.rowcount


def search(db: Session, criteria: SearchCriteria, *, offset: int, limit: int) -> list[AuditLog]:
    query = _filtered(select(AuditLog), criteria)
    query = query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
    return list(db.scalars(query.offset(offset).limit(limit)))


def count(db: Session, criteria: SearchCriteria) -> int:
    return db.scalar(_filtered(select(func.count(AuditLog.id)), criteria))


def get_by_id(db: Session, log_id: int) -> AuditLog | None:
    return db.get(AuditLog, log_id)


def _filtered(query: Select, criteria: SearchCriteria) -> Select:
    if criteria.occurred_from is not None:
        query = query.where(AuditLog.occurred_at >= criteria.occurred_from)
    if criteria.occurred_to is not None:
        query = query.where(AuditLog.occurred_at <= criteria.occurred_to)
    if criteria.entity_type is not None:
        query = query.where(AuditLog.entity_type == criteria.entity_type)
    if criteria.actor_ids is not None:
        query = query.where(AuditLog.actor_user_id.in_(criteria.actor_ids))
    if criteria.case_id is not None:
        query = query.where(AuditLog.case_id == criteria.case_id)
    if criteria.case_scope is not None:
        query = query.where(AuditLog.case_id.in_(criteria.case_scope))
    return query
