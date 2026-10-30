"""Logika bisnis modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

TODO(PBI-11, PBI-12, PBI-13): sisanya dikerjakan di tiket lain.
"""

import logging
import uuid
from collections.abc import Mapping
from decimal import Decimal

from app.modules.runs.cost_estimator import estimate_cost_idr
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.pricing import PriceCatalog
from app.modules.runs.schemas import CaseFilter, SummaryRequest, SummaryResponse
from app.modules.runs.summary_ports import (
    AnswerLengthPort,
    CaseCountPort,
    ProductInfo,
    ProductPort,
    SpendPort,
)
from app.shared.exceptions import InternalError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")

# Split tag yang dihitung untuk tiap pilihan. Pilihan baru cukup menambah baris.
_SPLITS_FOR_FILTER: Mapping[CaseFilter, tuple[str, ...]] = {
    CaseFilter.DEV: ("dev",),
    CaseFilter.TEST: ("test",),
    CaseFilter.BOTH: ("dev", "test"),
}


def eligible_case_count(counts: Mapping[str, int], case_filter: CaseFilter) -> int:
    """Jumlah kasus approved untuk pilihan dev, test, atau both (PBI-11 AC2).

    counts berkunci nama split tag. Split yang tidak ada dianggap nol.
    """
    return sum(counts.get(split, 0) for split in _SPLITS_FOR_FILTER[case_filter])


def total_calls(case_count: int, repeat_count: int) -> int:
    """Total panggilan ke produk AI: kasus kali pengulangan (PBI-11 AC3)."""
    return case_count * repeat_count


class SummaryService:
    """Ringkasan sebelum Mulai: kasus, panggilan, perkiraan biaya, cek anggaran.

    Semua yang berubah-ubah masuk lewat port, jadi service ini tidak mengenal
    tabel, HTTP, atau konfigurasi. Yang dihitung: PBI-11 AC2, AC3, AC5.
    """

    def __init__(
        self,
        *,
        products: ProductPort,
        cases: CaseCountPort,
        spend: SpendPort,
        history: AnswerLengthPort,
        prices: PriceCatalog,
        rates: ExchangeRateProvider,
        safety_margin_percent: Decimal,
        default_avg_answer_tokens: Decimal | None,
    ) -> None:
        self._products = products
        self._cases = cases
        self._spend = spend
        self._history = history
        self._prices = prices
        self._rates = rates
        self._margin = safety_margin_percent
        self._default_avg_tokens = default_avg_answer_tokens

    def summarize(self, request: SummaryRequest) -> SummaryResponse:
        product = self._require_automatic_product(request.product_id)
        counts = self._cases.approved_counts(set(request.suite_ids))
        case_count = eligible_case_count(counts, request.case_filter)
        if case_count == 0:
            raise ValidationError(
                "Tidak ada kasus yang disetujui dan bertag sesuai pilihan di suite ini.",
                code="NO_ELIGIBLE_CASES",
            )
        calls = total_calls(case_count, request.repeat_count)
        cost = estimate_cost_idr(
            total_calls=calls,
            avg_answer_tokens=self._avg_answer_tokens(product.id),
            usd_per_million_output_tokens=self._output_price(product),
            usd_idr_rate=self._rates.usd_idr(),
            safety_margin_percent=self._margin,
        )
        remaining = self._remaining_budget(product)
        if cost > remaining:
            raise ValidationError(
                f"Perkiraan biaya Rp{cost} melebihi sisa anggaran bulan ini untuk "
                f"produk {product.name} (Rp{remaining}). Pengukuran tidak dijalankan.",
                code="BUDGET_EXCEEDED",
                details={
                    "product_name": product.name,
                    "remaining_budget_idr": str(remaining),
                    "estimated_cost_idr": str(cost),
                },
            )
        return SummaryResponse(
            case_count=case_count,
            total_calls=calls,
            case_filter=request.case_filter,
            repeat_count=request.repeat_count,
            estimated_cost_idr=cost,
            remaining_budget_idr=remaining,
        )

    def _require_automatic_product(self, product_id: uuid.UUID) -> ProductInfo:
        product = self._products.get(product_id)
        if product is None:
            raise NotFoundError("Produk AI tidak ditemukan.", code="AI_PRODUCT_NOT_FOUND")
        if product.measurement_type != "automatic":
            raise ValidationError(
                "Produk manual tidak bisa diukur otomatis. Catat jawabannya secara manual.",
                code="PRODUCT_NOT_AUTOMATIC",
            )
        if not product.is_active:
            raise ValidationError("Produk AI ini sedang nonaktif.", code="PRODUCT_INACTIVE")
        return product

    def _output_price(self, product: ProductInfo) -> Decimal:
        """Harga produk didahulukan. Katalog konfigurasi hanya cadangan."""
        if product.price_output_usd_per_mtok is not None:
            return product.price_output_usd_per_mtok
        return self._prices.usd_per_million_output_tokens(product.provider_type, product.model_name)

    def _avg_answer_tokens(self, product_id: uuid.UUID) -> Decimal:
        average = self._history.average_answer_tokens(product_id)
        if average is not None:
            return average
        if self._default_avg_tokens is not None:
            return self._default_avg_tokens
        logger.error("No answer length history and no COST_DEFAULT_AVG_ANSWER_TOKENS")
        raise InternalError(
            "No answer length history and no default average is configured.",
            code="AVG_ANSWER_TOKENS_NOT_CONFIGURED",
        )

    def _remaining_budget(self, product: ProductInfo) -> Decimal:
        """Anggaran bulanan dikurangi yang terpakai, tidak pernah negatif."""
        spent = self._spend.spent_this_month_idr(product.id)
        remaining = max(product.monthly_budget_idr - spent, Decimal("0"))
        return remaining.quantize(_CENT)
