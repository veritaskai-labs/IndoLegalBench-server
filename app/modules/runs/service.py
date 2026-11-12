"""Logika bisnis modul runs.

PBI-11, PBI-12, PBI-13 Eksekusi pengukuran ke produk AI.

Ini satu-satunya pintu masuk yang boleh dipanggil modul lain. Service
tidak boleh menyentuh HTTP. Kalau aturan bisnis dilanggar, lempar
exception dari app.shared.exceptions.

TODO(PBI-11, PBI-12, PBI-13): sisanya dikerjakan di tiket lain.
"""

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.runs import repository
from app.modules.runs.adapters import NameAdapter
from app.modules.runs.call_log import month_start, monthly_limit
from app.modules.runs.case_progress import case_progress
from app.modules.runs.cost_estimator import estimate_cost_idr
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.models import SessionStatus
from app.modules.runs.pricing import PriceCatalog
from app.modules.runs.progress import compute_progress
from app.modules.runs.progress_ports import SessionProgressPort
from app.modules.runs.repository import CallLogRepository, SessionListRepository
from app.modules.runs.schemas import (
    CaseFilter,
    ProgressResponse,
    SessionListItem,
    StartRequest,
    StartResponse,
    SummaryRequest,
    SummaryResponse,
)
from app.modules.runs.session_list import SessionListService
from app.modules.runs.session_notice import Notifier, notify_session_ended
from app.modules.runs.session_ports import (
    JudgeLock,
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
    CaseTextPort,
    ProductInfo,
    ProductPort,
    SpendPort,
)
from app.modules.runs.tokenizer import TokenCounter
from app.shared.exceptions import InternalError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_PESAN_PRODUK_MANUAL = "Produk manual tidak bisa diukur otomatis. Catat jawabannya secara manual."
_PESAN_PENILAI_TIDAK_ADA = (
    "Provider penilai tidak ditemukan atau sudah nonaktif. Pilih penilai lain."
)

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
    Token masukan adalah jumlah token pertanyaan yang layak, dikali pengulangan
    (SCRUM-76, SCRUM-244).
    """

    def __init__(
        self,
        *,
        products: ProductPort,
        cases: CaseCountPort,
        texts: CaseTextPort,
        counter: TokenCounter,
        spend: SpendPort,
        history: AnswerLengthPort,
        prices: PriceCatalog,
        rates: ExchangeRateProvider,
        safety_margin_percent: Decimal,
        default_avg_answer_tokens: Decimal | None,
    ) -> None:
        self._products = products
        self._cases = cases
        self._texts = texts
        self._counter = counter
        self._spend = spend
        self._history = history
        self._prices = prices
        self._rates = rates
        self._margin = safety_margin_percent
        self._default_avg_tokens = default_avg_answer_tokens

    def summarize(self, request: SummaryRequest) -> SummaryResponse:
        product = self._require_automatic_product(request.product_id)
        suite_ids = set(request.suite_ids)
        counts = self._cases.approved_counts(suite_ids)
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
            total_input_tokens=self._input_tokens(
                product, suite_ids, request.case_filter, request.repeat_count
            ),
            usd_per_million_input_tokens=self._input_price(product),
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

    def _input_price(self, product: ProductInfo) -> Decimal:
        """Harga masukan produk didahulukan. Katalog masukan hanya cadangan."""
        if product.price_input_usd_per_mtok is not None:
            return product.price_input_usd_per_mtok
        return self._prices.usd_per_million_input_tokens(product.provider_type, product.model_name)

    def _input_tokens(
        self,
        product: ProductInfo,
        suite_ids: set[uuid.UUID],
        case_filter: CaseFilter,
        repeat_count: int,
    ) -> int:
        """Token pertanyaan yang layak, dihitung sekali, lalu dikali pengulangan.

        Jumlah kasus tetap dari CaseCountPort. Panjang kedua port tidak
        dicocokkan. Pertanyaan kosong dihitung nol dan tidak mengurangi kasus.
        Hanya teks pertanyaan yang dihitung, bukan templat prompt atau system
        prompt pada pemanggilan. Gemini, Anthropic, dan DeepSeek memakai
        cl100k_base yang sama, jadi perkiraan bisa lebih rendah dari tagihan.
        """
        texts = self._texts.approved_texts(suite_ids)
        per_question = 0
        for split in _SPLITS_FOR_FILTER[case_filter]:
            for question in texts.get(split, ()):
                per_question += self._counter.count(question, product.provider_type or "")
        return per_question * repeat_count

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
        return _remaining(monthly_limit(product), self._spend.spent_this_month_idr(product.id))


def _remaining(limit: Decimal, spent: Decimal) -> Decimal:
    """Batas dikurangi yang terpakai, dibulatkan ke sen dan tidak pernah negatif."""
    return max(limit - spent, Decimal("0")).quantize(_CENT)


class BudgetedProduct(Protocol):
    """Isian produk yang dibutuhkan untuk menghitung batas bulanan (monthly_limit)."""

    id: uuid.UUID
    measurement_type: str
    provider_type: str | None
    model_name: str | None
    monthly_budget_idr: Decimal | None


def remaining_budgets(
    db: Session, products: Sequence[BudgetedProduct], *, now: datetime
) -> dict[uuid.UUID, Decimal]:
    """Sisa anggaran bulan ini per produk Otomatis (PBI-11 SCRUM-210, PBI-23 AC6).

    Biaya semua produk dibaca dalam satu query. Produk Manual tidak punya
    anggaran (PBI-12 AC10), jadi tidak ada di hasil.
    """
    otomatis = [product for product in products if product.measurement_type == "automatic"]
    spent = CallLogRepository(db).sum_cost_since_by_product(
        [product.id for product in otomatis], month_start(now)
    )
    return {
        product.id: _remaining(monthly_limit(product), spent.get(product.id, Decimal("0")))
        for product in otomatis
    }


class ProgressService:
    """Angka layar progres satu pengukuran (PBI-12 AC5, AC6).

    Semua yang berubah-ubah masuk lewat port, jadi service ini tidak
    mengenal tabel maupun HTTP. Dirancang untuk di-poll tiap beberapa
    detik: satu agregasi sesi, satu produk, satu angka pemakaian bulan ini.
    Waktu sekarang juga masuk lewat clock, supaya perkiraan sisa waktu tidak
    bergantung pada kolom yang kebetulan ada di baris sesi.
    """

    def __init__(
        self,
        *,
        sessions: SessionProgressPort,
        products: ProductPort,
        spend: SpendPort,
        clock: Callable[[], datetime],
    ) -> None:
        self._sessions = sessions
        self._products = products
        self._spend = spend
        self._clock = clock

    def read(self, measurement_id: uuid.UUID) -> ProgressResponse:
        """Hitungan sesi menjadi angka yang dibaca layar progres."""
        hitungan = self._sessions.progress(measurement_id)
        if hitungan is None:
            raise NotFoundError(
                "Pengukuran tidak ditemukan.",
                code="MEASUREMENT_NOT_FOUND",
            )
        produk = self._products.get(hitungan.product_id)
        if produk is None:
            raise NotFoundError(
                "Produk AI tidak ditemukan.",
                code="AI_PRODUCT_NOT_FOUND",
            )
        if produk.monthly_budget_idr is None:
            raise InternalError(
                "Monthly budget is not configured for this product.",
                code="BUDGET_NOT_CONFIGURED",
            )
        angka = compute_progress(
            total_calls=hitungan.total_calls,
            completed_calls=hitungan.completed_calls,
            failed_calls=hitungan.failed_calls,
            started_at=hitungan.started_at,
            # Baris sesi tidak ikut diperbarui tiap panggilan, jadi waktu berjalan
            # dihitung dari jam sekarang, bukan dari updated_at (review #94).
            now=self._clock(),
            running_cost_idr=hitungan.running_cost_idr,
            monthly_budget_idr=produk.monthly_budget_idr,
            spent_this_month_idr=self._spend.spent_this_month_idr(produk.id),
        )
        kasus = case_progress(
            completed_cases=hitungan.completed_cases, total_cases=hitungan.total_cases
        )
        return ProgressResponse(
            **asdict(angka),
            **asdict(kasus),
            started_at=hitungan.started_at,
            updated_at=hitungan.updated_at,
        )


def product_has_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Riwayat pengukuran produk, untuk aturan jenis pengukuran PBI-23 di modul providers."""
    return repository.product_has_sessions(db, product_id)


def session_counts(db: Session, product_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Jumlah sesi per produk untuk daftar produk Admin (PBI-23 AC6), 0 bila belum pernah."""
    counts = repository.count_sessions_by_product(db, product_ids)
    return {product_id: counts.get(product_id, 0) for product_id in product_ids}


def session_labels(db: Session, session_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Label sesi untuk kolom Objek audit log (F-06), termasuk sesi yang diarsipkan."""
    if not session_ids:
        return {}
    return repository.session_labels(db, session_ids)


def latest_session(db: Session) -> SessionListItem | None:
    """Sesi terbaru yang belum diarsipkan, untuk latest_run di dashboard (PBI-19 AC1)."""
    return SessionListService(sessions=SessionListRepository(db), names=NameAdapter(db)).latest()


class StartSessionService:
    """Tombol Mulai: validasi ulang, lalu sesi masuk antrean (PBI-11 AC1, AC3, AC5).

    Validasinya SummaryService yang sama dengan Lihat ringkasan, karena produk,
    kasus, atau anggaran bisa berubah di antara keduanya (D3 langkah 20). Sesi
    tidak langsung berjalan: worker yang menjalankan saat gilirannya.
    """

    def __init__(
        self,
        *,
        summary: SummaryService,
        sessions: SessionQueueStore,
        product_lock: ProductLock,
        judge_lock: JudgeLock,
    ) -> None:
        self._summary = summary
        self._sessions = sessions
        self._product_lock = product_lock
        self._judge_lock = judge_lock

    def start(self, request: StartRequest, *, created_by: uuid.UUID) -> StartResponse:
        self._summary.summarize(request)
        # Kontrak #90: kunci produk dan baca ulang jenisnya di transaksi yang sama dengan
        # INSERT sesi, supaya ganti jenis yang bersamaan tidak meloloskan sesi (D1 C6).
        if self._product_lock.lock_measurement_type(request.product_id) != "automatic":
            raise ValidationError(_PESAN_PRODUK_MANUAL, code="PRODUCT_NOT_AUTOMATIC")
        ensure_judge_available(self._judge_lock, request.judge_provider_id)
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


def ensure_judge_available(judge_lock: JudgeLock, judge_id: uuid.UUID | None) -> None:
    """Penilai yang dipilih harus ada dan aktif; dipakai sesi otomatis dan manual (SCRUM-258).

    Penilai dikunci dan dibaca ulang di transaksi yang sama dengan INSERT sesi, seperti
    produk, supaya penilai yang dinonaktifkan bersamaan tidak tersimpan. Tanpa penilai tidak
    ada yang dikunci: penilai dipilih saat menilai (K-14, SCRUM-281).
    """
    if judge_id is not None and not judge_lock.lock_available(judge_id):
        raise ValidationError(
            _PESAN_PENILAI_TIDAK_ADA, code="JUDGE_PROVIDER_NOT_AVAILABLE", field="judge_provider_id"
        )


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
        notifier: Notifier | None = None,
    ) -> None:
        self._sessions = sessions
        self._snapshots = snapshots
        self._products = products
        self._clock = clock
        self._notifier = notifier

    def take_next(self) -> uuid.UUID | None:
        """Id sesi yang baru berjalan, atau None bila ada sesi berjalan atau antrean kosong.

        Sesi yang tidak bisa dimulai ditandai failed lalu sesi berikutnya dicoba,
        supaya satu sesi rusak tidak menahan antrean selamanya. Itu termasuk galat
        tak terduga: tanpa ditandai, oldest_queued() memberi sesi yang sama setiap putaran.
        """
        if self._sessions.running_exists():
            return None
        while (waiting := self._sessions.oldest_queued()) is not None:
            if waiting.prepared:
                # SCRUM-267 AC8: snapshot dan hasil lama dipakai lagi. Galat sengaja tidak
                # ditandai failed, supaya galat sesaat tidak membuang sesi yang sudah punya hasil.
                return waiting.id if self._sessions.reopen(waiting.id, at=self._clock()) else None
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
                self._mark_failed(waiting)
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
        # D4: failed = kesalahan fatal.
        logger.warning("Measurement session %s failed before start: %s", waiting.id, alasan)
        self._mark_failed(waiting)

    def _mark_failed(self, waiting: WaitingSession) -> None:
        """Tandai failed, lalu kabari pemulainya (AC10, SCRUM-210).

        Notifikasi hanya bila status benar-benar berubah, supaya sesi yang sudah
        ditangani worker lain tidak mendapat notifikasi ganda.
        """
        changed = self._sessions.fail(waiting.id, finished_at=self._clock())
        if changed and self._notifier is not None:
            notify_session_ended(
                self._notifier,
                session_id=waiting.id,
                label=waiting.new.label,
                status=SessionStatus.FAILED,
                started_by=waiting.new.created_by,
            )
