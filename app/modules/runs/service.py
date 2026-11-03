"""Logika bisnis modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

TODO(PBI-11, PBI-12, PBI-13): sisanya dikerjakan di tiket lain.
"""

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.call_log import monthly_limit
from app.modules.runs.cost_estimator import estimate_cost_idr
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.pricing import PriceCatalog
from app.modules.runs.schemas import (
    CaseFilter,
    StartRequest,
    StartResponse,
    SummaryRequest,
    SummaryResponse,
)
from app.modules.runs.session_ports import (
    NewSession,
    ProductCopy,
    ProductCopySource,
    ProductLock,
    SessionQueueStore,
    SnapshotItem,
    SnapshotSource,
    WaitingSession,
)
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
_PESAN_PRODUK_MANUAL = "Produk manual tidak bisa diukur otomatis. Catat jawabannya secara manual."

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
            raise ValidationError(_PESAN_PRODUK_MANUAL, code="PRODUCT_NOT_AUTOMATIC")
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
        remaining = max(monthly_limit(product) - spent, Decimal("0"))
        return remaining.quantize(_CENT)


def product_has_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Riwayat pengukuran produk, untuk aturan jenis pengukuran PBI-23 di modul providers."""
    return repository.product_has_sessions(db, product_id)


class StartSessionService:
    """Tombol Mulai: validasi ulang, lalu sesi masuk antrean (PBI-11 AC1, AC3, AC5).

    Validasinya SummaryService yang sama dengan Lihat ringkasan, karena produk,
    kasus, atau anggaran bisa berubah di antara keduanya (D3 langkah 20). Sesi
    tidak langsung berjalan: worker yang menjalankan saat gilirannya.
    """

    def __init__(
        self, *, summary: SummaryService, sessions: SessionQueueStore, product_lock: ProductLock
    ) -> None:
        self._summary = summary
        self._sessions = sessions
        self._product_lock = product_lock

    def start(self, request: StartRequest, *, created_by: uuid.UUID) -> StartResponse:
        self._summary.summarize(request)
        # Kontrak #90: kunci produk dan baca ulang jenisnya di transaksi yang sama dengan
        # INSERT sesi, supaya ganti jenis yang bersamaan tidak meloloskan sesi (D1 C6).
        if self._product_lock.lock_measurement_type(request.product_id) != "automatic":
            raise ValidationError(_PESAN_PRODUK_MANUAL, code="PRODUCT_NOT_AUTOMATIC")
        queued = self._sessions.create_queued(
            NewSession(
                label=request.label,
                purpose=request.purpose,
                product_id=request.product_id,
                suite_ids=tuple(request.suite_ids),
                case_filter=request.case_filter,
                repetitions=request.repeat_count,
                created_by=created_by,
                judge_provider_id=request.judge_provider_id,
            )
        )
        return StartResponse(session_id=queued.id, status=queued.status, queued_at=queued.queued_at)


def _sekarang() -> datetime:
    return datetime.now(UTC)


def items_for_filter(items: list[SnapshotItem], case_filter: CaseFilter) -> list[SnapshotItem]:
    """Kasus yang ikut dibekukan untuk pilihan dev, test, atau both, urutan tetap (AC2)."""
    splits = _SPLITS_FOR_FILTER[case_filter]
    return [item for item in items if item.split_tag in splits]


class SessionQueue:
    """Giliran antrean: sesi Menunggu tertua mulai berjalan (PBI-11 AC2, AC3, AC6).

    Memenuhi QueuePort worker SCRUM-208 (take_next). Snapshot kasus dan salinan
    produk baru diambil saat giliran tiba, bukan saat Mulai ditekan, sesuai
    D3 langkah 24-27 dan AC "snapshot dibuat saat pengukuran dijalankan".
    """

    def __init__(
        self,
        *,
        sessions: SessionQueueStore,
        snapshots: SnapshotSource,
        products: ProductCopySource,
        clock: Callable[[], datetime] = _sekarang,
    ) -> None:
        self._sessions = sessions
        self._snapshots = snapshots
        self._products = products
        self._clock = clock

    def take_next(self) -> uuid.UUID | None:
        """Id sesi yang baru berjalan, atau None bila ada sesi berjalan atau antrean kosong.

        Sesi yang tidak bisa dimulai ditandai failed lalu sesi berikutnya dicoba,
        supaya satu sesi rusak tidak menahan antrean selamanya. Itu termasuk galat
        tak terduga: tanpa ditandai, oldest_queued() memberi sesi yang sama setiap putaran.
        """
        if self._sessions.running_exists():
            return None
        while (waiting := self._sessions.oldest_queued()) is not None:
            try:
                prepared = self._prepare(waiting)
                if prepared is None:
                    continue
                items, product = prepared
                started = self._sessions.start(
                    waiting.id, items=items, product=product, started_at=self._clock()
                )
            except Exception:
                logger.exception("Measurement session %s failed before start", waiting.id)
                self._sessions.fail(waiting.id, finished_at=self._clock())
                continue
            # False berarti worker lain lebih dulu; sesi ini tetap Menunggu giliran berikutnya.
            return waiting.id if started else None
        return None

    def _prepare(self, waiting: WaitingSession) -> tuple[list[SnapshotItem], ProductCopy] | None:
        """Snapshot dan salinan produk, atau None bila sesi sudah ditandai failed."""
        try:
            product = self._products.copy(waiting.new.product_id)
        except NotFoundError:
            return self._fail(waiting, "produk AI sudah dihapus sejak Mulai ditekan")
        except ValidationError:
            # PRODUCT_INACTIVE dari ProductCopyAdapter: Admin menonaktifkan produk saat sesi menunggu.
            return self._fail(waiting, "produk AI dinonaktifkan sejak Mulai ditekan")
        items = items_for_filter(
            self._snapshots.approved_items(waiting.new.suite_ids), waiting.new.case_filter
        )
        if not items:
            return self._fail(waiting, "tidak ada lagi kasus layak untuk filter sesi ini")
        return items, product

    def _fail(self, waiting: WaitingSession, alasan: str) -> None:
        # D4: failed = kesalahan fatal. Notifikasi ke pemulai milik SCRUM-210.
        logger.warning("Measurement session %s failed before start: %s", waiting.id, alasan)
        self._sessions.fail(waiting.id, finished_at=self._clock())
