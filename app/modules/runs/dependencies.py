"""Perakitan dependency FastAPI untuk modul runs (PBI-11, SCRUM-206/207/244)."""

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.modules.notifications.service import DbNotifier
from app.modules.runs.adapters import (
    CallLogAnswerHistory,
    CallLogSpend,
    CaseCountAdapter,
    CaseTextAdapter,
    JudgeLockAdapter,
    NameAdapter,
    ProductAdapter,
    ProductCopyAdapter,
    ProductLockAdapter,
    SessionProgressAdapter,
    SnapshotSourceAdapter,
    SuiteGuardAdapter,
)
from app.modules.runs.call_log import CallLogService
from app.modules.runs.exchange_rate import (
    ConfiguredExchangeRate,
    ExchangeRateProvider,
    JisdorExchangeRate,
)
from app.modules.runs.manual_answers import ManualAnswerService
from app.modules.runs.manual_evidence import ManualEvidenceService
from app.modules.runs.manual_evidence_repository import DbEvidenceStorage, EvidenceRepository
from app.modules.runs.manual_session import ManualSessionService
from app.modules.runs.manual_session_status import ManualSessionStatusService
from app.modules.runs.pricing import ConfigPriceCatalog
from app.modules.runs.repository import (
    CallLogRepository,
    ManualSessionRepository,
    SessionListRepository,
    SessionQueueRepository,
)
from app.modules.runs.service import (
    ProgressService,
    SessionQueue,
    StartSessionService,
    SummaryService,
)
from app.modules.runs.session_detail import SessionDetailService
from app.modules.runs.session_list import SessionListService
from app.modules.runs.session_ports import SessionQueueStore
from app.modules.runs.tokenizer import TiktokenCounter
from app.modules.runs.worker import run_next
from app.shared.config import Settings, get_settings
from app.shared.database import get_db


@lru_cache
def get_exchange_rate() -> ExchangeRateProvider:
    """Satu objek untuk seluruh proses, supaya kurs harian tidak diambil ulang tiap request."""
    settings = get_settings()
    return JisdorExchangeRate(
        url=settings.jisdor_api_url,
        api_key=settings.jisdor_api_key,
        fallback=ConfiguredExchangeRate(settings.cost_usd_idr_fallback_rate),
    )


def get_summary_service(
    db: Session = Depends(get_db),
    rates: ExchangeRateProvider = Depends(get_exchange_rate),
    settings: Settings = Depends(get_settings),
) -> SummaryService:
    return SummaryService(
        products=ProductAdapter(db),
        cases=CaseCountAdapter(db),
        texts=CaseTextAdapter(db),
        counter=TiktokenCounter(),
        spend=CallLogSpend(db),
        history=CallLogAnswerHistory(db),
        prices=ConfigPriceCatalog(
            settings.cost_model_prices,
            input_prices=settings.cost_model_input_prices,
        ),
        rates=rates,
        safety_margin_percent=settings.cost_safety_margin_percent,
        default_avg_answer_tokens=settings.cost_default_avg_answer_tokens,
    )


def get_progress_service(db: Session = Depends(get_db)) -> ProgressService:
    """Satu agregasi sesi, satu produk, satu angka pemakaian bulan ini."""
    return ProgressService(
        sessions=SessionProgressAdapter(db),
        products=ProductAdapter(db),
        spend=CallLogSpend(db),
        clock=lambda: datetime.now(UTC),
    )


def get_session_list_service(db: Session = Depends(get_db)) -> SessionListService:
    return SessionListService(sessions=SessionListRepository(db), names=NameAdapter(db))


def get_session_detail_service(db: Session = Depends(get_db)) -> SessionDetailService:
    return SessionDetailService(sessions=SessionListRepository(db), names=NameAdapter(db))


def get_session_store(db: Session = Depends(get_db)) -> SessionQueueStore:
    """Antrean sesi di measurement_sessions (#89), dibuat per request dari session database."""
    return SessionQueueRepository(db)


def get_start_service(
    db: Session = Depends(get_db),
    summary: SummaryService = Depends(get_summary_service),
    sessions: SessionQueueStore = Depends(get_session_store),
) -> StartSessionService:
    """Ringkasan, kunci produk, dan INSERT sesi memakai satu session database (satu transaksi)."""
    return StartSessionService(
        summary=summary,
        sessions=sessions,
        product_lock=ProductLockAdapter(db),
        judge_lock=JudgeLockAdapter(db),
    )


def get_manual_session_service(db: Session = Depends(get_db)) -> ManualSessionService:
    """Kunci produk, snapshot, dan INSERT sesi manual memakai satu session database (PBI-22)."""
    return ManualSessionService(
        sessions=ManualSessionRepository(db),
        snapshots=SnapshotSourceAdapter(db),
        products=ProductCopyAdapter(db),
        product_lock=ProductLockAdapter(db),
        suites=SuiteGuardAdapter(db),
        judge_lock=JudgeLockAdapter(db),
    )


def get_manual_answer_service(db: Session = Depends(get_db)) -> ManualAnswerService:
    """Simpan jawaban, timpa, dan riwayatnya lewat repository (review #119)."""
    return ManualAnswerService(ManualSessionRepository(db))


def get_manual_status_service(db: Session = Depends(get_db)) -> ManualSessionStatusService:
    """Status, Selesai, dan halaman sesi manual lewat repository (review #116)."""
    return ManualSessionStatusService(ManualSessionRepository(db))


def get_manual_evidence_service(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> ManualEvidenceService:
    """Baris bukti dan isi berkasnya di satu session database (PBI-22 SCRUM-233)."""
    return ManualEvidenceService(
        EvidenceRepository(db),
        DbEvidenceStorage(db),
        max_bytes=settings.manual_evidence_max_bytes,
        visibility=settings.manual_evidence_visibility,
    )


def build_session_queue(db: Session) -> SessionQueue:
    """Antrean untuk runner worker (QueuePort SCRUM-208), di luar request HTTP.

    Membaca tabel yang sama dengan POST /measurements.
    """
    return SessionQueue(
        sessions=SessionQueueRepository(db),
        snapshots=SnapshotSourceAdapter(db),
        products=ProductCopyAdapter(db),
        # Sesi pendek sendiri di database yang sama, supaya notifikasi tidak ikut
        # transaksi antrean dan tidak bergantung pada pengaturan global.
        notifier=DbNotifier(sessionmaker(bind=db.get_bind())),
    )


def get_call_log_service(
    db: Session,
    rates: ExchangeRateProvider,
) -> CallLogService:
    """Satu-satunya tempat produksi yang membuat CallLogService."""
    return CallLogService(
        products=ProductAdapter(db),
        repository=CallLogRepository(db),
        rates=rates,
        clock=lambda: datetime.now(UTC),
    )


def run_queued_measurement(
    db: Session,
    queue,
    store,
    adapter,
    product_id,
    *,
    rates: ExchangeRateProvider | None = None,
    **kwargs,
):
    """Pemanggil run_next. Tanpa ini worker tidak menulis call log."""
    return run_next(
        queue,
        store,
        adapter,
        call_log=get_call_log_service(db, rates or get_exchange_rate()),
        product_id=product_id,
        **kwargs,
    )
