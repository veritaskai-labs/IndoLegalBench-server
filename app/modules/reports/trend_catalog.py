"""Pembaca nama produk dari database untuk tren. PBI-17 SCRUM-84 SCRUM-331."""

import uuid

from sqlalchemy.orm import Session


class SqlProductCatalog:
    def __init__(self, db: Session) -> None:
        self._db = db

    def name_of(self, product_id: uuid.UUID) -> str | None:
        return None
