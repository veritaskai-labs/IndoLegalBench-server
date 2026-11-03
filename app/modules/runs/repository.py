"""Akses database modul runs.

ATURAN: hanya runs/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.

TODO(PBI-11, PBI-12, PBI-13): query lain menyusul sesuai kebutuhan service.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, func, null, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    AttemptStatus,
    CallLog,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    MeasurementSessionSuite,
    SessionProductCopy,
    SessionPurpose,
    SessionStatus,
    SessionType,
    SplitFilter,
)
from app.modules.runs.schemas import CaseFilter, MeasurementPurpose
from app.modules.runs.session_ports import (
    NewSession,
    ProductCopy,
    QueuedSession,
    SnapshotItem,
    WaitingSession,
)


def product_has_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Sesi yang diarsipkan tetap terhitung: datanya tidak pernah dihapus (AC10)."""
    return bool(db.scalar(select(exists().where(MeasurementSession.product_id == product_id))))


class CallLogRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, row: CallLog) -> None:
        self._db.add(row)
        self._db.flush()

    def sum_cost_since(self, product_id: uuid.UUID, start: datetime) -> Decimal:
        total = self._db.scalar(
            select(func.coalesce(func.sum(CallLog.cost_idr), 0)).where(
                CallLog.product_id == product_id,
                CallLog.started_at >= start,
                CallLog.cost_idr.is_not(None),
            )
        )
        return Decimal(total)

    def output_token_average(self, product_id: uuid.UUID) -> Decimal | None:
        average = self._db.scalar(
            select(func.avg(CallLog.output_tokens)).where(
                CallLog.product_id == product_id,
                CallLog.output_tokens.is_not(None),
            )
        )
        if average is None:
            return None
        return Decimal(average)

    def attempt_counts(self, product_id: uuid.UUID) -> tuple[int, int]:
        total = self._db.scalar(
            select(func.count()).select_from(CallLog).where(CallLog.product_id == product_id)
        )
        failed = self._db.scalar(
            select(func.count())
            .select_from(CallLog)
            .where(CallLog.product_id == product_id, CallLog.failure_kind.is_not(None))
        )
        return int(total or 0), int(failed or 0)


def _sekarang() -> datetime:
    return datetime.now(UTC)


# Nama index partial "satu sesi otomatis running" dari migrasi #89. SQLite menyebut
# kolomnya, PostgreSQL menyebut nama index-nya.
_SATU_RUNNING = (
    "uq_measurement_sessions_one_running_automatic",
    "measurement_sessions.session_type",
)


def _sesi_lain_running(exc: IntegrityError) -> bool:
    return any(tanda in str(exc.orig) for tanda in _SATU_RUNNING)


# Hanya sesi ini yang antre untuk worker otomatis (D2, AC3, AC10 arsip disembunyikan).
_ANTREAN_OTOMATIS = (
    MeasurementSession.status == SessionStatus.QUEUED,
    MeasurementSession.session_type == SessionType.AUTOMATIC,
    MeasurementSession.archived_at.is_(None),
)


class SessionQueueRepository:
    """Antrean sesi di measurement_sessions (SCRUM-207), memenuhi port SessionQueueStore."""

    def __init__(self, db: Session, clock: Callable[[], datetime] = _sekarang) -> None:
        self._db = db
        self._clock = clock

    def create_queued(self, new: NewSession) -> QueuedSession:
        # Dikembalikan apa adanya: SQLite membuang zona waktu saat baris dibaca ulang.
        queued_at = self._clock()
        session = MeasurementSession(
            label=new.label,
            purpose=SessionPurpose(new.purpose.value),
            session_type=SessionType.AUTOMATIC,
            product_id=new.product_id,
            repetitions=new.repetitions,
            split_filter=SplitFilter(new.case_filter.value),
            status=SessionStatus.QUEUED,
            created_by=new.created_by,
            queued_at=queued_at,
            judge_provider_id=new.judge_provider_id,
        )
        self._db.add(session)
        self._db.flush()
        self._db.add_all(
            MeasurementSessionSuite(session_id=session.id, suite_id=suite_id)
            for suite_id in new.suite_ids
        )
        self._db.commit()
        return QueuedSession(id=session.id, status=SessionStatus.QUEUED, queued_at=queued_at)

    def running_exists(self) -> bool:
        return bool(
            self._db.scalar(
                select(
                    exists().where(
                        MeasurementSession.status == SessionStatus.RUNNING,
                        MeasurementSession.session_type == SessionType.AUTOMATIC,
                    )
                )
            )
        )

    def oldest_queued(self) -> WaitingSession | None:
        # id memecah seri bila dua sesi masuk antrean pada waktu yang sama.
        session = self._db.scalars(
            select(MeasurementSession)
            .where(*_ANTREAN_OTOMATIS)
            .order_by(MeasurementSession.queued_at, MeasurementSession.id)
            .limit(1)
        ).first()
        if session is None:
            return None
        # Tabel suite tidak menyimpan urutan pilihan, jadi urut id supaya snapshot stabil.
        suite_ids = self._db.scalars(
            select(MeasurementSessionSuite.suite_id)
            .where(MeasurementSessionSuite.session_id == session.id)
            .order_by(MeasurementSessionSuite.suite_id)
        ).all()
        return WaitingSession(
            id=session.id,
            new=NewSession(
                label=session.label,
                purpose=MeasurementPurpose(session.purpose.value),
                product_id=session.product_id,
                suite_ids=tuple(suite_ids),
                case_filter=CaseFilter(session.split_filter.value),
                repetitions=session.repetitions,
                created_by=session.created_by,
                judge_provider_id=session.judge_provider_id,
            ),
        )

    def start(
        self,
        session_id: uuid.UUID,
        *,
        items: Sequence[SnapshotItem],
        product: ProductCopy,
        started_at: datetime,
    ) -> bool:
        """D3 langkah 25-28 dalam satu transaksi: running, snapshot, salinan, attempts.

        False bila sesi tidak lagi menunggu atau sesi otomatis lain sudah running;
        tidak ada yang tersimpan. Galat lain apa pun dibatalkan lalu dinaikkan, supaya
        session database runner tetap bisa dipakai dan SessionQueue menandai sesinya failed.
        """
        try:
            # UPDATE bersyarat: worker kedua yang membaca sesi yang sama mendapat 0 baris.
            dimulai = self._db.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id == session_id, *_ANTREAN_OTOMATIS)
                .values(status=SessionStatus.RUNNING, started_at=started_at)
                .execution_options(synchronize_session=False)
            ).rowcount
            if dimulai != 1:
                self._db.rollback()
                return False
            self._freeze(session_id, items, product)
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            if isinstance(exc, IntegrityError) and _sesi_lain_running(exc):
                return False
            raise
        return True

    def fail(self, session_id: uuid.UUID, *, finished_at: datetime) -> bool:
        # fail() dipanggil setelah sesuatu gagal. Di PostgreSQL bacaan yang galat
        # membatalkan seluruh transaksi, jadi buang dulu sebelum menandai sesi.
        self._db.rollback()
        gagal = self._db.execute(
            update(MeasurementSession)
            .where(MeasurementSession.id == session_id, *_ANTREAN_OTOMATIS)
            .values(status=SessionStatus.FAILED, finished_at=finished_at)
            .execution_options(synchronize_session=False)
        ).rowcount
        self._db.commit()
        return gagal == 1

    def _freeze(
        self, session_id: uuid.UUID, items: Sequence[SnapshotItem], product: ProductCopy
    ) -> None:
        snapshot = CaseSnapshot(session_id=session_id)
        self._db.add(snapshot)
        self._db.flush()
        rows = [
            CaseSnapshotItem(
                snapshot_id=snapshot.id,
                case_id=item.case_id,
                case_version_id=item.case_version_id,
                body=item.content,
            )
            for item in items
        ]
        self._db.add_all(rows)
        self._db.add(_product_copy_row(session_id, product))
        self._db.flush()
        repetitions = self._db.scalar(
            select(MeasurementSession.repetitions).where(MeasurementSession.id == session_id)
        )
        self._db.add_all(
            Attempt(
                session_id=session_id,
                snapshot_item_id=row.id,
                repetition_no=nomor,
                source=AttemptSource.AUTOMATIC,
                status=AttemptStatus.PENDING,
            )
            for row in rows
            for nomor in range(1, repetitions + 1)
        )
        self._db.flush()


def _product_copy_row(session_id: uuid.UUID, product: ProductCopy) -> SessionProductCopy:
    """D2 session_product_copies."""
    return SessionProductCopy(
        session_id=session_id,
        product_id=product.product_id,
        name=product.name,
        measurement_type=product.measurement_type,
        provider_type=product.provider_type,
        base_url=product.base_url,
        model_name=product.model_name,
        # null() supaya kosong menjadi SQL NULL, bukan JSON 'null' (pelajaran PBI-23).
        model_params=product.model_params if product.model_params is not None else null(),
        price_input_usd_per_mtok=product.price_input_usd_per_mtok,
        price_output_usd_per_mtok=product.price_output_usd_per_mtok,
        monthly_budget_idr=product.monthly_budget_idr,
    )
