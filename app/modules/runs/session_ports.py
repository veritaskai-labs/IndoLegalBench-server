"""Port penyimpanan sesi pengukuran (PBI-11, SCRUM-207).

Tabel measurement_sessions dan measurement_session_suites (D2) milik SCRUM-205
belum ada. Service hanya berbicara lewat port ini, jadi adapter tabel nanti
cukup memenuhi kontrak yang sama, tanpa mengubah service atau router.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from app.modules.runs.schemas import CaseFilter, MeasurementPurpose, SessionStatus


@dataclass(frozen=True)
class NewSession:
    """Pilihan pemulai di halaman Buat Pengukuran, yang disimpan bersama sesi."""

    label: str
    purpose: MeasurementPurpose
    product_id: uuid.UUID
    suite_ids: tuple[uuid.UUID, ...]
    case_filter: CaseFilter
    repetitions: int
    created_by: uuid.UUID
    # Dibekukan #89 setelah sesi keluar dari queued: penilai dipilih saat Mulai (PBI-14 AC6).
    judge_provider_id: uuid.UUID | None = None


@dataclass(frozen=True)
class QueuedSession:
    id: uuid.UUID
    status: SessionStatus
    queued_at: datetime


@dataclass(frozen=True)
class WaitingSession:
    id: uuid.UUID
    new: NewSession


@dataclass(frozen=True)
class SnapshotItem:
    """Satu kasus yang dibekukan (D2 case_snapshot_items).

    case_id adalah reference id kasus live, bukan id baris snapshot (AC6).
    content adalah salinan persis versi disetujui terbaru.
    """

    case_id: uuid.UUID
    case_version_id: uuid.UUID
    split_tag: str
    content: dict[str, Any]


@dataclass(frozen=True)
class ProductCopy:
    """Salinan produk saat sesi berjalan (D2 session_product_copies), tanpa kredensial.

    rate_limit_per_minute tidak ikut karena session_product_copies (#89) belum
    punya kolomnya. model_version_at_start belum diketahui sebelum panggilan
    pertama, jadi diisi worker, bukan di sini.
    """

    product_id: uuid.UUID
    name: str
    measurement_type: str
    provider_type: str | None
    base_url: str | None
    model_name: str | None
    model_params: dict[str, Any] | None
    price_input_usd_per_mtok: Decimal | None
    price_output_usd_per_mtok: Decimal | None
    monthly_budget_idr: Decimal | None


class SessionQueueStore(Protocol):
    def create_queued(self, new: NewSession) -> QueuedSession:
        """Simpan sesi berstatus queued dengan queued_at sekarang (AC3, D3 langkah 21)."""
        raise NotImplementedError

    def running_exists(self) -> bool:
        """Ada sesi otomatis yang sedang running."""
        raise NotImplementedError

    def oldest_queued(self) -> WaitingSession | None:
        """Sesi queued dengan queued_at paling awal, atau None bila antrean kosong."""
        raise NotImplementedError

    def start(
        self,
        session_id: uuid.UUID,
        *,
        items: Sequence[SnapshotItem],
        product: ProductCopy,
        started_at: datetime,
    ) -> bool:
        """Status running + snapshot + salinan produk sekaligus (D3 langkah 25-27).

        Adapter tabel melakukannya dalam satu transaksi. False bila sesi tidak
        lagi queued atau sesi lain sudah running (partial unique index D2).
        """
        raise NotImplementedError

    def fail(self, session_id: uuid.UUID, *, finished_at: datetime) -> bool:
        """Sesi queued menjadi failed (D4, kesalahan fatal sebelum mulai). False bila bukan queued."""
        raise NotImplementedError


class ProductLock(Protocol):
    def lock_measurement_type(self, product_id: uuid.UUID) -> str:
        """Kunci produk sampai transaksi selesai, lalu jenisnya (kontrak #90, D1 C6)."""
        raise NotImplementedError


class JudgeLock(Protocol):
    def lock_available(self, judge_provider_id: uuid.UUID) -> bool:
        """Kunci baris penilai sampai transaksi selesai, lalu True bila ada dan aktif (SCRUM-258)."""
        raise NotImplementedError


class SnapshotSource(Protocol):
    def approved_items(self, suite_ids: Sequence[uuid.UUID]) -> list[SnapshotItem]:
        """Versi disetujui terbaru setiap kasus di suite itu, urut suite lalu kode kasus."""
        raise NotImplementedError


class ProductCopySource(Protocol):
    def copy(self, product_id: uuid.UUID) -> ProductCopy:
        """Data produk saat ini, tanpa kredensial."""
        raise NotImplementedError
