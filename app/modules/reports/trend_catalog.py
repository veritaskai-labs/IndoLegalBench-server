"""Pembaca nama produk dari database untuk tren. PBI-17 SCRUM-84 SCRUM-331."""

import uuid

from sqlalchemy.orm import Session

from app.modules.providers import repository as providers


class SqlProductCatalog:
    """Memenuhi ProductCatalog. Produk yang sudah dihapus dianggap tidak ada."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def name_of(self, product_id: uuid.UUID) -> str | None:
        product = providers.get_by_id(self._db, product_id)
        return product.name if product else None
