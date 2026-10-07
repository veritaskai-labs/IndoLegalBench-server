"""Akses database modul providers.

ATURAN: hanya providers/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid

from sqlalchemy.orm import Session

from app.modules.providers.models import AiProduct


def get_by_id(db: Session, product_id: uuid.UUID) -> AiProduct | None:
    """Mengembalikan None untuk produk yang sudah dihapus (deleted_at IS NOT NULL)."""
    return (
        db.query(AiProduct)
        .filter(AiProduct.id == product_id, AiProduct.deleted_at.is_(None))
        .first()
    )


def get_by_name(db: Session, name: str) -> AiProduct | None:
    """Hanya mencari produk yang belum dihapus, sesuai partial unique index."""
    return (
        db.query(AiProduct)
        .filter(AiProduct.name == name, AiProduct.deleted_at.is_(None))
        .first()
    )


def list_products(db: Session, *, is_active: bool | None) -> list[AiProduct]:
    """Produk yang sudah dihapus tidak pernah masuk daftar."""
    query = db.query(AiProduct).filter(AiProduct.deleted_at.is_(None))
    if is_active is not None:
        query = query.filter(AiProduct.is_active == is_active)
    return query.order_by(AiProduct.name).all()


def create(db: Session, product: AiProduct) -> AiProduct:
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def save(db: Session, product: AiProduct) -> AiProduct:
    db.commit()
    db.refresh(product)
    return product
