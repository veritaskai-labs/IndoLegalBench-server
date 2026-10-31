"""Port nyata untuk ringkasan sesi pengukuran (PBI-11, SCRUM-206).

Modul runs hanya menjangkau cases, suites, dan providers lewat fungsi service
mereka, tidak lewat tabel atau repository.
"""

import uuid
from collections.abc import Mapping
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.cases import service as cases_service
from app.modules.providers import service as providers_service
from app.modules.runs.summary_ports import ProductInfo
from app.modules.suites import service as suites_service
from app.shared.exceptions import NotFoundError


class CaseCountAdapter:
    """Kasus approved per split tag, setelah memastikan semua suite ada."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def approved_counts(self, suite_ids: set[uuid.UUID]) -> Mapping[str, int]:
        suites_service.require_existing(self._db, suite_ids)
        counts = cases_service.count_approved_by_split(self._db, suite_ids)
        return {split.value: total for split, total in counts.items()}


class ProductAdapter:
    """Produk AI sebagai ProductInfo, tanpa alamat dan kredensialnya.

    Tipe pengukuran dan harga output dibaca dari produk. Produk manual tidak
    punya provider dan model, jadi field itu bisa kosong. Harga kosong berarti
    service memakai katalog harga dari konfigurasi.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, product_id: uuid.UUID) -> ProductInfo | None:
        try:
            product = providers_service.get_product(self._db, product_id)
        except NotFoundError:
            return None
        return ProductInfo(
            id=product.id,
            name=product.name,
            measurement_type=product.measurement_type.value,
            is_active=product.is_active,
            provider_type=product.provider_type.value if product.provider_type else None,
            model_name=product.model_name,
            monthly_budget_idr=product.monthly_budget_idr,
            price_output_usd_per_mtok=product.price_output_usd_per_mtok,
        )


class PendingSpend:
    """Pengeluaran bulan ini. Log panggilan belum ada (SCRUM-209), jadi selalu nol."""

    def spent_this_month_idr(self, _product_id: uuid.UUID) -> Decimal:
        return Decimal("0.00")


class PendingAnswerHistory:
    """Panjang jawaban sebelumnya. Log panggilan belum ada (SCRUM-209), jadi kosong."""

    def average_answer_tokens(self, _product_id: uuid.UUID) -> Decimal | None:
        return None
