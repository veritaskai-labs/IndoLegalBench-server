"""Akses database tabel judge_providers (SCRUM-258).

ATURAN: hanya providers/judge_service.py yang boleh memanggil file ini.
Isi file ini murni query, tanpa logika bisnis.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.providers.models import JudgeProvider


def get_by_id(db: Session, judge_id: uuid.UUID, *, for_share: bool = False) -> JudgeProvider | None:
    """for_share mengunci baris (FOR SHARE) sampai transaksi pemanggil selesai.

    Kunci hanya berdasarkan id, tidak menyaring is_active: nonaktifkan yang bersamaan
    harus ditunggu lalu terbaca, bukan dilewati (pelajaran SCRUM-222).
    """
    query = db.query(JudgeProvider).filter(JudgeProvider.id == judge_id)
    if for_share:
        query = query.with_for_update(read=True).populate_existing()
    return query.first()


def get_by_name(db: Session, name: str) -> JudgeProvider | None:
    return db.query(JudgeProvider).filter(JudgeProvider.name == name).first()


def list_all(db: Session) -> list[JudgeProvider]:
    """Semua penilai: yang aktif lebih dulu, lalu urut nama."""
    return (
        db.query(JudgeProvider).order_by(JudgeProvider.is_active.desc(), JudgeProvider.name).all()
    )


def list_options(db: Session) -> list[sa.Row]:
    """Penilai aktif, hanya kolom pilihan; ciphertext tidak ikut terbaca dari database."""
    return (
        db.query(
            JudgeProvider.id,
            JudgeProvider.name,
            JudgeProvider.provider_type,
            JudgeProvider.model_name,
        )
        .filter(JudgeProvider.is_active == sa.true())
        .order_by(JudgeProvider.name)
        .all()
    )


def create(db: Session, judge: JudgeProvider) -> JudgeProvider:
    db.add(judge)
    db.commit()
    db.refresh(judge)
    return judge


def save(db: Session, judge: JudgeProvider) -> JudgeProvider:
    db.commit()
    db.refresh(judge)
    return judge
