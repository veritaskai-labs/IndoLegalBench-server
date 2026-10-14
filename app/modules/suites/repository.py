"""Akses database modul suites.

ATURAN: hanya suites/service.py yang boleh memanggil file ini.
Modul lain tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis dan tanpa HTTP.
"""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.modules.suites.models import Suite, SuiteSnapshot, SuiteSnapshotItem, SuiteStatus


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


def create_snapshot(db: Session, snapshot: SuiteSnapshot) -> SuiteSnapshot:
    """Insert a snapshot and the items already attached to it."""
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def list_snapshots(
    db: Session, suite_id: uuid.UUID, *, offset: int, limit: int
) -> list[SuiteSnapshot]:
    """Newest snapshot first. A tie on created_at breaks on id."""
    return (
        db.query(SuiteSnapshot)
        .filter(SuiteSnapshot.suite_id == suite_id)
        .order_by(SuiteSnapshot.created_at.desc(), SuiteSnapshot.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_snapshots(db: Session, suite_id: uuid.UUID) -> int:
    return db.query(SuiteSnapshot).filter(SuiteSnapshot.suite_id == suite_id).count()


def item_counts(db: Session, snapshot_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many items each snapshot holds. Missing ids are left out."""
    if not snapshot_ids:
        return {}
    rows = (
        db.query(SuiteSnapshotItem.snapshot_id, func.count(SuiteSnapshotItem.id))
        .filter(SuiteSnapshotItem.snapshot_id.in_(snapshot_ids))
        .group_by(SuiteSnapshotItem.snapshot_id)
        .all()
    )
    return {snapshot_id: int(jumlah) for snapshot_id, jumlah in rows}


def get_snapshot(db: Session, snapshot_id: uuid.UUID) -> SuiteSnapshot | None:
    """The snapshot with its items loaded, or None."""
    return (
        db.query(SuiteSnapshot)
        .options(selectinload(SuiteSnapshot.items))
        .filter(SuiteSnapshot.id == snapshot_id)
        .one_or_none()
    )
