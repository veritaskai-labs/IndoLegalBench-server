"""Port nyata untuk ringkasan, log panggilan, dan antrean sesi (PBI-11, SCRUM-206/207/209).

Modul runs hanya menjangkau cases, suites, dan providers lewat fungsi service
mereka, tidak lewat tabel atau repository.
"""

import dataclasses
import itertools
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.cases import service as cases_service
from app.modules.providers import service as providers_service
from app.modules.runs.call_log import month_start
from app.modules.runs.repository import CallLogRepository
from app.modules.runs.schemas import SessionStatus
from app.modules.runs.session_ports import (
    NewSession,
    ProductCopy,
    QueuedSession,
    SnapshotItem,
    WaitingSession,
)
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
            price_input_usd_per_mtok=product.price_input_usd_per_mtok,
        )


class SnapshotSourceAdapter:
    """Kasus yang dibekukan saat sesi berjalan (PBI-11 AC2, AC6).

    approved_copies_for_suite sudah memakai versi disetujui terbaru, termasuk
    untuk kasus yang sedang diedit, dan meninggalkan kasus yang belum pernah
    disetujui. Penyaringan dev/test dilakukan service.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def approved_items(self, suite_ids: Sequence[uuid.UUID]) -> list[SnapshotItem]:
        items: list[SnapshotItem] = []
        for suite_id in suite_ids:
            for copy in cases_service.approved_copies_for_suite(self._db, suite_id):
                items.append(
                    SnapshotItem(
                        case_id=copy["case_id"],
                        case_version_id=copy["case_version_id"],
                        split_tag=copy["body"]["sections"]["split_tag"],
                        content=copy["body"],
                    )
                )
        return items


class ProductCopyAdapter:
    """Salinan produk untuk sesi (D2 session_product_copies). Kredensial tidak dibaca."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def copy(self, product_id: uuid.UUID) -> ProductCopy:
        product = providers_service.get_product(self._db, product_id)
        return ProductCopy(
            product_id=product.id,
            name=product.name,
            measurement_type=product.measurement_type.value,
            provider_type=product.provider_type.value if product.provider_type else None,
            base_url=product.base_url,
            model_name=product.model_name,
            model_params=product.model_params.model_dump() if product.model_params else None,
            rate_limit_per_minute=product.rate_limit_per_minute,
            price_input_usd_per_mtok=product.price_input_usd_per_mtok,
            price_output_usd_per_mtok=product.price_output_usd_per_mtok,
            monthly_budget_idr=product.monthly_budget_idr,
        )


class CallLogSpend:
    """Pengeluaran bulan ini dari call_logs."""

    def __init__(self, db: Session) -> None:
        self._rows = CallLogRepository(db)

    def spent_this_month_idr(self, product_id: uuid.UUID) -> Decimal:
        now = datetime.now(ZoneInfo("Asia/Jakarta"))
        return self._rows.sum_cost_since(product_id, month_start(now))


class CallLogAnswerHistory:
    """Rata-rata token jawaban dari call_logs."""

    def __init__(self, db: Session) -> None:
        self._rows = CallLogRepository(db)

    def average_answer_tokens(self, product_id: uuid.UUID) -> Decimal | None:
        return self._rows.output_token_average(product_id)


def _sekarang() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class StoredSession:
    id: uuid.UUID
    new: NewSession
    status: SessionStatus
    queue_order: int
    queued_at: datetime
    started_at: datetime | None = None
    snapshot: tuple[SnapshotItem, ...] = ()
    product_copy: ProductCopy | None = None


class InMemorySessionQueue:
    """Sesi di memori proses. SEMENTARA, diganti adapter tabel SCRUM-205.

    Hanya supaya POST /measurements bisa dicoba sebelum measurement_sessions
    ada: isinya hilang saat server restart dan tidak terbagi antar worker.
    Lock menjaga queue_order tetap unik saat dua permintaan datang bersamaan.
    """

    def __init__(self, clock: Callable[[], datetime] = _sekarang) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._urutan = itertools.count(1)
        self._sesi: dict[uuid.UUID, StoredSession] = {}

    def create_queued(self, new: NewSession) -> QueuedSession:
        with self._lock:
            sesi = StoredSession(
                id=uuid.uuid4(),
                new=new,
                status=SessionStatus.QUEUED,
                queue_order=next(self._urutan),
                queued_at=self._clock(),
            )
            self._sesi[sesi.id] = sesi
        return QueuedSession(id=sesi.id, status=sesi.status, queue_order=sesi.queue_order)

    def running_exists(self) -> bool:
        return any(sesi.status == SessionStatus.RUNNING for sesi in self._sesi.values())

    def oldest_queued(self) -> WaitingSession | None:
        menunggu = [sesi for sesi in self.get_all() if sesi.status == SessionStatus.QUEUED]
        if not menunggu:
            return None
        return WaitingSession(id=menunggu[0].id, new=menunggu[0].new)

    def start(
        self,
        session_id: uuid.UUID,
        *,
        items: Sequence[SnapshotItem],
        product: ProductCopy,
        started_at: datetime,
    ) -> bool:
        # Pemeriksaan dan perubahan di bawah satu lock, meniru satu transaksi tabel.
        with self._lock:
            sesi = self._sesi.get(session_id)
            if sesi is None or sesi.status != SessionStatus.QUEUED or self.running_exists():
                return False
            self._sesi[session_id] = dataclasses.replace(
                sesi,
                status=SessionStatus.RUNNING,
                started_at=started_at,
                snapshot=tuple(items),
                product_copy=product,
            )
        return True

    def get(self, session_id: uuid.UUID) -> StoredSession | None:
        return self._sesi.get(session_id)

    def get_all(self) -> list[StoredSession]:
        """Urut queue_order, yang paling lama menunggu lebih dulu."""
        return sorted(self._sesi.values(), key=lambda sesi: sesi.queue_order)
