"""Akses database modul grading.

ATURAN: hanya grading/service.py yang boleh memanggil file ini. Modul lain tidak boleh
mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.grading.models import (
    RubricAspect,
    RubricLevel,
    RubricLevelDescriptor,
    RubricVersion,
)


def max_version_no(db: Session) -> int:
    """Nomor versi terbesar, 0 kalau belum ada versi."""
    return db.scalar(sa.select(sa.func.coalesce(sa.func.max(RubricVersion.version_no), 0)))


def active_version_for_update(db: Session) -> RubricVersion | None:
    """Versi yang sedang aktif, dikunci sampai transaksi selesai (SELECT ... FOR UPDATE)."""
    query = sa.select(RubricVersion).where(RubricVersion.is_active).with_for_update()
    return db.scalars(query.execution_options(populate_existing=True)).first()


def add_and_flush(db: Session, rows: Iterable[object]) -> None:
    """INSERT baris sekarang, supaya urutan tulis mengikuti urutan pemanggilan."""
    db.add_all(rows)
    db.flush()


def get_version(db: Session, version_id: uuid.UUID) -> RubricVersion | None:
    return db.get(RubricVersion, version_id)


def list_versions(db: Session) -> list[tuple[RubricVersion, int, int]]:
    """Semua versi, terbaru di atas, dengan jumlah aspek dan jumlah level."""
    aspek = (
        sa.select(sa.func.count())
        .where(RubricAspect.rubric_version_id == RubricVersion.id)
        .scalar_subquery()
    )
    level = (
        sa.select(sa.func.count())
        .where(RubricLevel.rubric_version_id == RubricVersion.id)
        .scalar_subquery()
    )
    query = sa.select(RubricVersion, aspek, level).order_by(RubricVersion.version_no.desc())
    return [
        (versi, jumlah_aspek, jumlah_level)
        for versi, jumlah_aspek, jumlah_level in db.execute(query)
    ]


def aspects_of(db: Session, version_id: uuid.UUID) -> list[RubricAspect]:
    query = (
        sa.select(RubricAspect)
        .where(RubricAspect.rubric_version_id == version_id)
        .order_by(RubricAspect.sort_order)
    )
    return list(db.scalars(query))


def levels_of(db: Session, version_id: uuid.UUID) -> list[RubricLevel]:
    query = (
        sa.select(RubricLevel)
        .where(RubricLevel.rubric_version_id == version_id)
        .order_by(RubricLevel.score.desc())
    )
    return list(db.scalars(query))


def descriptors_of(db: Session, level_ids: list[uuid.UUID]) -> list[RubricLevelDescriptor]:
    query = sa.select(RubricLevelDescriptor).where(
        RubricLevelDescriptor.rubric_level_id.in_(level_ids)
    )
    return list(db.scalars(query))
