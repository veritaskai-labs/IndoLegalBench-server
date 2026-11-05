"""Akses database modul runs.

ATURAN: hanya runs/service.py yang boleh memanggil file ini. Modul lain
tidak boleh mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.

TODO(PBI-11, PBI-12, PBI-13): query lain menyusul sesuai kebutuhan service.
"""

import uuid
from collections import defaultdict
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
    StopReason,
)
from app.modules.runs.schemas import CaseFilter, MeasurementPurpose
from app.modules.runs.session_detail import AttemptRow, CaseRow, SessionDetailRow
from app.modules.runs.session_list import SessionRow
from app.modules.runs.session_ports import (
    NewSession,
    ProductCopy,
    QueuedSession,
    SnapshotItem,
    WaitingSession,
)
from app.modules.runs.session_transitions import invalid_transition, sources_of
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError


def product_has_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Sesi yang diarsipkan tetap terhitung: datanya tidak pernah dihapus (AC10)."""
    return bool(db.scalar(select(exists().where(MeasurementSession.product_id == product_id))))


def count_sessions_by_product(
    db: Session, product_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Satu query untuk banyak produk, sesi arsip ikut terhitung. Produk tanpa sesi tidak ada."""
    if not product_ids:
        return {}
    rows = db.execute(
        select(MeasurementSession.product_id, func.count())
        .where(MeasurementSession.product_id.in_(product_ids))
        .group_by(MeasurementSession.product_id)
    )
    return dict(rows.tuples().all())


def session_labels(db: Session, session_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    rows = db.execute(
        select(MeasurementSession.id, MeasurementSession.label).where(
            MeasurementSession.id.in_(session_ids)
        )
    )
    return dict(rows.tuples().all())


def get_session_for_update(db: Session, session_id: uuid.UUID) -> MeasurementSession | None:
    """Kunci baris sesi sampai commit, supaya dua arsip bersamaan tidak saling menimpa."""
    return db.scalars(
        select(MeasurementSession)
        .where(MeasurementSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


class SessionListRepository:
    """Daftar dan detail sesi. Jumlah query tetap, berapa pun sesi dan kasusnya (PBI-11)."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def active_sessions(self) -> list[SessionRow]:
        sessions = self._db.scalars(
            select(MeasurementSession)
            .where(MeasurementSession.archived_at.is_(None))
            .order_by(MeasurementSession.created_at.desc(), MeasurementSession.id)
        ).all()
        return self._rows(sessions)

    def latest_active(self) -> SessionRow | None:
        """Sesi terbaru yang belum diarsipkan, untuk latest_run di dashboard (PBI-19 AC1)."""
        session = self._db.scalars(
            select(MeasurementSession)
            .where(MeasurementSession.archived_at.is_(None))
            .order_by(MeasurementSession.created_at.desc(), MeasurementSession.id)
            .limit(1)
        ).first()
        rows = self._rows([session] if session is not None else [])
        return rows[0] if rows else None

    def detail(self, session_id: uuid.UUID) -> SessionDetailRow | None:
        """Sesi arsip ikut terbaca: arsip hanya menyembunyikan dari daftar (AC10)."""
        session = self._db.get(MeasurementSession, session_id)
        if session is None:
            return None
        (summary,) = self._rows([session])
        return SessionDetailRow(
            summary=summary,
            purpose=SessionPurpose(session.purpose).value,
            split_filter=SplitFilter(session.split_filter).value,
            snapshot_created_at=self._db.scalar(
                select(CaseSnapshot.created_at).where(CaseSnapshot.session_id == session_id)
            ),
            started_at=session.started_at,
            finished_at=session.finished_at,
            archived_at=session.archived_at,
            stopped_at=session.stopped_at,
            stop_reason=None if session.stop_reason is None else StopReason(session.stop_reason),
            accepted_as_is_at=session.accepted_as_is_at,
            accepted_as_is_by=session.accepted_as_is_by,
            accepted_as_is_reason=session.accepted_as_is_reason,
            cases=self._cases(session_id),
        )

    def _cases(self, session_id: uuid.UUID) -> list[CaseRow]:
        items = self._db.scalars(
            select(CaseSnapshotItem)
            .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
            .where(CaseSnapshot.session_id == session_id)
        ).all()
        attempts: dict[uuid.UUID, list[AttemptRow]] = defaultdict(list)
        for attempt in self._db.scalars(
            select(Attempt)
            .where(Attempt.session_id == session_id)
            .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
        ):
            attempts[attempt.snapshot_item_id].append(
                AttemptRow(
                    repetition_no=attempt.repetition_no,
                    status=AttemptStatus(attempt.status),
                    answer_text=attempt.answer_text,
                    model_version=attempt.model_version,
                )
            )
        return [
            CaseRow(
                snapshot_item_id=item.id,
                case_id=item.case_id,
                body=item.body,
                attempts=attempts.get(item.id, []),
            )
            for item in items
        ]

    def _rows(self, sessions: Sequence[MeasurementSession]) -> list[SessionRow]:
        if not sessions:
            return []
        ids = [session.id for session in sessions]
        attempts = self._attempt_counts(ids)
        cases = self._case_counts(ids)
        return [
            SessionRow(
                id=session.id,
                label=session.label,
                created_at=session.created_at,
                created_by=session.created_by,
                product_id=session.product_id,
                repetitions=session.repetitions,
                status=SessionStatus(session.status),
                case_count=cases.get(session.id),
                attempt_counts=attempts.get(session.id, {}),
            )
            for session in sessions
        ]

    def _attempt_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
        rows = self._db.execute(
            select(Attempt.session_id, Attempt.status, func.count())
            .where(Attempt.session_id.in_(ids))
            .group_by(Attempt.session_id, Attempt.status)
        )
        counts: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
        for session_id, status, count in rows:
            counts[session_id][AttemptStatus(status).value] = count
        return counts

    def _case_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Hanya sesi yang sudah punya snapshot. Snapshot kosong bernilai 0."""
        rows = self._db.execute(
            select(CaseSnapshot.session_id, func.count(CaseSnapshotItem.id))
            .outerjoin(CaseSnapshotItem, CaseSnapshotItem.snapshot_id == CaseSnapshot.id)
            .where(CaseSnapshot.session_id.in_(ids))
            .group_by(CaseSnapshot.session_id)
        )
        return dict(rows.tuples().all())


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

    def sum_cost_since_by_product(
        self, product_ids: Sequence[uuid.UUID], start: datetime
    ) -> dict[uuid.UUID, Decimal]:
        """Satu query untuk banyak produk. Produk tanpa biaya tidak ada di hasil."""
        if not product_ids:
            return {}
        rows = self._db.execute(
            select(CallLog.product_id, func.sum(CallLog.cost_idr))
            .where(
                CallLog.product_id.in_(product_ids),
                CallLog.started_at >= start,
                CallLog.cost_idr.is_not(None),
            )
            .group_by(CallLog.product_id)
        )
        return {product_id: Decimal(total) for product_id, total in rows}

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


_SELESAI = frozenset(
    {SessionStatus.FINISHED, SessionStatus.FINISHED_WITH_FAILURES, SessionStatus.FAILED}
)


def _kolom_waktu(
    target: SessionStatus, at: datetime, stop_reason: StopReason | None
) -> dict[str, object]:
    """Kolom waktu yang ikut berubah bersama status (D2)."""
    if target is SessionStatus.RUNNING:
        return {"stopped_at": None, "stop_reason": None}
    kolom: dict[str, object] = {}
    if target in _SELESAI:
        kolom["finished_at"] = at
    if stop_reason is not None:
        kolom["stopped_at"] = at
        kolom["stop_reason"] = stop_reason
    return kolom


class SessionStatusRepository:
    """Satu-satunya jalan tulis status sesi yang mengikuti tabel D4 (SCRUM-214).

    UPDATE bersyarat: status sekarang harus salah satu asal yang sah untuk target.
    Dua penulis bersamaan tidak bisa sama-sama menang, karena PostgreSQL membaca
    ulang syaratnya setelah kunci baris penulis pertama lepas.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def change_status(
        self,
        session_id: uuid.UUID,
        target: SessionStatus | str,
        *,
        at: datetime,
        stop_reason: StopReason | str | None = None,
    ) -> None:
        target = SessionStatus(target)
        reason = None if stop_reason is None else StopReason(stop_reason)
        if target is SessionStatus.PAUSED and reason is None:
            raise ValidationError(
                "Sesi yang berhenti wajib punya penyebab (user, budget, atau restart).",
                code="STOP_REASON_REQUIRED",
                field="stop_reason",
            )
        diubah = self._db.execute(
            update(MeasurementSession)
            .where(
                MeasurementSession.id == session_id,
                MeasurementSession.status.in_(sources_of(target)),
                # Diterima apa adanya = selesai sepenuhnya (AC9): tidak berpindah ke mana pun.
                MeasurementSession.accepted_as_is_at.is_(None),
            )
            .values(status=target, **_kolom_waktu(target, at, reason))
            .execution_options(synchronize_session=False)
        ).rowcount
        if diubah == 1:
            self._db.commit()
            return
        self._db.rollback()
        sekarang = self._db.execute(
            select(MeasurementSession.status, MeasurementSession.accepted_as_is_at).where(
                MeasurementSession.id == session_id
            )
        ).first()
        if sekarang is None:
            raise NotFoundError("Sesi pengukuran tidak ditemukan.")
        status, diterima = sekarang
        if diterima is not None:
            raise ConflictError(
                "Sesi sudah diterima apa adanya. Hapus tandanya dulu untuk mencoba ulang.",
                code="SESSION_ACCEPTED",
            )
        raise invalid_transition(status, target)


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
            # queued -> failed ada di tabel D4; syarat antrean di sini lebih sempit dari
            # change_status, supaya antrean tidak pernah menggagalkan sesi yang sudah running.
            .values(
                status=SessionStatus.FAILED,
                finished_at=finished_at,
                stopped_at=finished_at,
                stop_reason=StopReason.ERROR,
            )
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
