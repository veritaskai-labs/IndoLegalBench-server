"""Akses database modul suites.

ATURAN: hanya suites/service.py yang boleh memanggil file ini.
Modul lain tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis dan tanpa HTTP.
"""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.suites.models import Suite, SuiteStatus


def get_by_id(db: Session, suite_id: uuid.UUID) -> Suite | None:
    suite = db.get(Suite, suite_id)
    if suite is None or suite.deleted_at is not None:
        return None
    return suite


def get_by_name(db: Session, name: str) -> Suite | None:
    """Cari nama tanpa peduli kapital, termasuk baris yang sudah dihapus.

    Nama yang pernah dipakai tetap milik baris itu (AC2), jadi pencarian
    ini sengaja tidak menyaring deleted_at.
    """
    return db.query(Suite).filter(func.lower(Suite.name) == name.lower()).first()


def list_by_status(
    db: Session, status: SuiteStatus, *, offset: int = 0, limit: int = 20
) -> list[Suite]:
    return (
        db.query(Suite)
        .filter(Suite.deleted_at.is_(None), Suite.status == status)
        .order_by(func.lower(Suite.name))
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_by_status(db: Session, status: SuiteStatus) -> int:
    return db.query(Suite).filter(Suite.deleted_at.is_(None), Suite.status == status).count()


def create(db: Session, suite: Suite) -> Suite:
    db.add(suite)
    db.commit()
    db.refresh(suite)
    return suite


def save(db: Session, suite: Suite) -> Suite:
    db.commit()
    db.refresh(suite)
    return suite
