"""Port yang dipakai ringkasan sesi pengukuran (PBI-11, SCRUM-206).

Tabel sesi dan log panggilan (SCRUM-205, SCRUM-209) belum ada, jadi service
ringkasan hanya berbicara lewat port ini. Implementasi nyata menyusul.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ProductInfo:
    """Data produk AI yang dibutuhkan ringkasan, bukan baris tabelnya.

    Untuk produk manual, field khusus otomatis kosong.
    """

    id: uuid.UUID
    name: str
    measurement_type: str
    is_active: bool
    provider_type: str | None
    model_name: str | None
    monthly_budget_idr: Decimal | None
    price_output_usd_per_mtok: Decimal | None


class ProductPort(Protocol):
    def get(self, product_id: uuid.UUID) -> ProductInfo | None:
        """Produk, atau None bila tidak ada atau sudah dihapus."""
        raise NotImplementedError


class CaseCountPort(Protocol):
    def approved_counts(self, suite_ids: set[uuid.UUID]) -> Mapping[str, int]:
        """Kasus approved per nama split tag. Suite yang tidak ada menjadi 404."""
        raise NotImplementedError


class SpendPort(Protocol):
    def spent_this_month_idr(self, product_id: uuid.UUID) -> Decimal:
        """Biaya produk yang sudah terpakai bulan ini, dalam rupiah."""
        raise NotImplementedError


class AnswerLengthPort(Protocol):
    def average_answer_tokens(self, product_id: uuid.UUID) -> Decimal | None:
        """Rata-rata token jawaban pengukuran sebelumnya, atau None bila belum ada."""
        raise NotImplementedError
