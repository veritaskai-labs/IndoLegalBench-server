"""Akses database modul providers.

ATURAN: hanya providers/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.providers.models import AiProduct, MeasurementType


def get_by_id(db: Session, product_id: uuid.UUID) -> AiProduct | None:
    """Mengembalikan None untuk produk yang sudah dihapus (deleted_at IS NOT NULL)."""
    return (
        db.query(AiProduct)
        .filter(AiProduct.id == product_id, AiProduct.deleted_at.is_(None))
        .first()
    )


def names_by_ids(db: Session, product_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Nama produk per id, sengaja termasuk produk yang sudah dihapus."""
    rows = db.query(AiProduct.id, AiProduct.name).filter(AiProduct.id.in_(product_ids)).all()
    return dict(rows)


def get_by_name(db: Session, name: str) -> AiProduct | None:
    """Mencari produk berdasarkan nama, termasuk yang sudah dihapus (nama unik selamanya)."""
    return db.query(AiProduct).filter(AiProduct.name == name).first()


def has_measurement_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Mengecek apakah produk sudah memiliki sesi pengukuran (riwayat).

    Tabel measurement_sessions dibuat pada PBI-11. Fungsi ini memeriksa
    keberadaan tabel terlebih dahulu secara aman agar tidak gagal bila tabel
    belum dibuat.
    """
    bind = db.get_bind()
    insp = sa.inspect(bind)
    if "measurement_sessions" not in insp.get_table_names():
        return False
    query = sa.text("SELECT 1 FROM measurement_sessions WHERE product_id = :product_id LIMIT 1")
    return db.execute(query, {"product_id": str(product_id)}).scalar() is not None


def list_products(
    db: Session, *, is_active: bool | None, measurement_type: MeasurementType | None = None
) -> list[AiProduct]:
    """Produk yang sudah dihapus tidak pernah masuk daftar. None berarti tanpa saringan."""
    query = db.query(AiProduct).filter(AiProduct.deleted_at.is_(None))
    if is_active is not None:
        query = query.filter(AiProduct.is_active == is_active)
    if measurement_type is not None:
        query = query.filter(AiProduct.measurement_type == measurement_type)
    return query.order_by(AiProduct.name).all()


def list_options(db: Session, *, measurement_type: MeasurementType) -> list[sa.Row]:
    """Produk aktif satu jenis, hanya kolom pilihan (PBI-23 AC7, AC8).

    Sengaja tidak memuat seluruh baris: daftar ini dibaca Kontributor, jadi
    ciphertext kredensial dan catatan akses tidak ikut terbaca dari database.
    """
    return (
        db.query(AiProduct.id, AiProduct.name, AiProduct.measurement_type)
        .filter(
            AiProduct.deleted_at.is_(None),
            AiProduct.is_active == sa.true(),
            AiProduct.measurement_type == measurement_type,
        )
        .order_by(AiProduct.name)
        .all()
    )


def create(db: Session, product: AiProduct) -> AiProduct:
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def save(db: Session, product: AiProduct) -> AiProduct:
    db.commit()
    db.refresh(product)
    return product
