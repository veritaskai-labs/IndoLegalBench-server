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

from app.modules.runs.models import AttemptSource
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
    # Produk manual (PBI-23 AC3, sesi manual PBI-22): penyedia dan situs tempat tim legal
    # bertanya. Kosong untuk produk otomatis.
    vendor_name: str | None = None
    site_url: str | None = None
    manual_params: str | None = None


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


class SuiteGuard(Protocol):
    def require_measurable(self, suite_ids: Sequence[uuid.UUID]) -> None:
        """Suite ada dan tidak diarsipkan: 404 bila hilang, 409 SUITE_ARCHIVED (SCRUM-206)."""
        raise NotImplementedError


class ManualSessionStore(Protocol):
    def create_manual(
        self, new: NewSession, *, items: Sequence[SnapshotItem], product: ProductCopy
    ) -> uuid.UUID:
        """Sesi manual Belum terisi + suite + snapshot + salinan produk + percobaan kosong.

        Satu transaksi, di session database yang sama dengan kunci produk (kontrak #90).
        Percobaan dibuat untuk setiap kasus x percobaan, supaya progres "X dari N" dan
        status D8 dihitung dari baris yang sama (PBI-22 AC8).
        """
        raise NotImplementedError


@dataclass(frozen=True)
class ManualSessionRow:
    """Kepala halaman sesi manual: sesi dan salinan produknya (PBI-22 AC8, AC12, AC13)."""

    id: uuid.UUID
    label: str
    purpose: str
    status: SessionStatus
    case_filter: str
    repetitions: int
    created_by: uuid.UUID
    created_at: datetime
    answer_date_from: datetime | None
    answer_date_to: datetime | None
    finalized_at: datetime | None
    finalized_by: uuid.UUID | None
    product_name: str
    vendor_name: str | None
    site_url: str | None
    manual_params: str | None


@dataclass(frozen=True)
class ManualCaseRow:
    """Satu kasus snapshot; body berbentuk snapshot_body() PBI-8."""

    snapshot_item_id: uuid.UUID
    case_id: uuid.UUID
    body: dict[str, Any]


@dataclass(frozen=True)
class ManualAttemptRow:
    """Satu percobaan manual: terisi bila filled_at ada (D9)."""

    id: uuid.UUID
    snapshot_item_id: uuid.UUID
    repetition_no: int
    filled: bool
    answered_at_platform: datetime | None
    source: AttemptSource = AttemptSource.MANUAL
    answer_md: str | None = None
    platform_refused: bool = False
    note: str | None = None
    filled_by: uuid.UUID | None = None
    filled_at: datetime | None = None


@dataclass(frozen=True)
class ManualAnswerHistoryRow:
    """Satu versi jawaban manual yang sudah ditimpa (answer_history, PBI-22 AC5)."""

    id: uuid.UUID
    attempt_id: uuid.UUID
    answer_md: str | None
    filled_by: uuid.UUID | None
    filled_at: datetime
    obtained_at: datetime | None
    platform_refused: bool
    note: str | None
    overwritten_by: uuid.UUID
    overwritten_at: datetime


class ManualStatusStore(Protocol):
    """Penyimpanan status sesi manual (PBI-22 SCRUM-230, review #116)."""

    def lock_manual(self, session_id: uuid.UUID) -> SessionStatus | None:
        """Kunci baris sesi sampai commit; None bila tidak ada atau bukan sesi manual."""
        raise NotImplementedError

    def manual_attempts(self, session_id: uuid.UUID) -> list[ManualAttemptRow]:
        """Semua percobaan sesi, urut kasus lalu nomor percobaan."""
        raise NotImplementedError

    def set_answer_dates(
        self, session_id: uuid.UUID, start: datetime | None, end: datetime | None
    ) -> None:
        raise NotImplementedError

    def change_status(
        self,
        session_id: uuid.UUID,
        target: SessionStatus,
        *,
        at: datetime,
        finalized_by: uuid.UUID | None = None,
        commit: bool,
    ) -> None:
        """Lewat SessionStatusRepository.change_status: tabel transisi dan audit (PBI-18)."""
        raise NotImplementedError

    def read_manual(self, session_id: uuid.UUID) -> ManualSessionRow | None:
        raise NotImplementedError

    def snapshot_cases(self, session_id: uuid.UUID) -> list[ManualCaseRow]:
        raise NotImplementedError


class ManualAnswerStore(ManualStatusStore, Protocol):
    """Penyimpanan jawaban manual dan riwayat timpanya (PBI-22 AC4 sampai AC7, SCRUM-231).

    Tidak ada metode ubah atau hapus riwayat: versi lama hanya bisa ditambah, jadi
    aturan simpan-saja terjaga oleh tipe, bukan oleh kesepakatan.
    """

    def is_manual_session(self, session_id: uuid.UUID) -> bool:
        """True hanya bila sesinya ada dan bertipe manual."""
        raise NotImplementedError

    def answer_slot(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
        *,
        lock: bool = False,
    ) -> ManualAttemptRow | None:
        """Satu percobaan manual sesi; None bila kasus atau nomornya tidak ada."""
        raise NotImplementedError

    def case_attempt_count(self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID) -> int:
        """Jumlah percobaan manual untuk satu kasus, untuk memisahkan dua galat AC."""
        raise NotImplementedError

    def archive_answer(
        self,
        attempt_id: uuid.UUID,
        *,
        overwritten_by: uuid.UUID,
        overwritten_at: datetime,
    ) -> None:
        """Pindahkan jawaban yang sekarang ke answer_history sebelum ditimpa (AC5)."""
        raise NotImplementedError

    def write_answer(
        self,
        attempt_id: uuid.UUID,
        *,
        answer_md: str | None,
        platform_refused: bool,
        note: str | None,
        obtained_at: datetime,
        filled_by: uuid.UUID,
        filled_at: datetime,
    ) -> ManualAttemptRow:
        """Tulis jawaban apa adanya dan catat pengisi serta waktunya (AC4, AC7)."""
        raise NotImplementedError

    def answer_history(self, attempt_id: uuid.UUID) -> list[ManualAnswerHistoryRow]:
        """Versi lama satu jawaban, terlama dulu (AC5)."""
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError
