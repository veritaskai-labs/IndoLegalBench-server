"""Katalog produk untuk tren, di SQLite. PBI-17 SCRUM-84 SCRUM-331.

Produk yang sudah dihapus dianggap tidak ada, sama dengan modul providers.
"""

import uuid
from datetime import UTC, datetime

from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.reports.trend_catalog import SqlProductCatalog
from tests.reports_factory import buat_admin


def _produk(db, nama: str, **ubah) -> AiProduct:
    produk = AiProduct(
        name=nama,
        created_by=buat_admin(db).id,
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Penyedia Uji",
        site_url="https://contoh.test",
        **ubah,
    )
    db.add(produk)
    db.flush()
    return produk


def test_returns_the_name_of_an_existing_product(db_session):
    # Arrange
    produk = _produk(db_session, "Produk Tren")

    # Act
    nama = SqlProductCatalog(db_session).name_of(produk.id)

    # Assert
    assert nama == "Produk Tren"


def test_returns_none_for_an_unknown_product(db_session):
    # Act
    nama = SqlProductCatalog(db_session).name_of(uuid.uuid4())

    # Assert
    assert nama is None


def test_returns_none_for_a_deleted_product(db_session):
    # Arrange
    produk = _produk(db_session, "Produk Lama", deleted_at=datetime.now(UTC))

    # Act
    nama = SqlProductCatalog(db_session).name_of(produk.id)

    # Assert
    assert nama is None
