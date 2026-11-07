"""Akses database modul runs.

ATURAN: service dan runtime di dalam modul runs memakai repository ini.
Modul lain masuk lewat service/runtime, bukan mengimpor repository milik modul lain.

Isi file ini murni query, tanpa logika bisnis.

TODO(PBI-11, PBI-12, PBI-13): query lain menyusul sesuai kebutuhan service.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Row, exists, func, null, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.runs.models import (
    AnswerHistory,
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
    ManualAnswerHistoryRow,
    ManualAttemptRow,
    ManualCaseRow,
    ManualSessionRow,
    NewSession,
    ProductCopy,
    QueuedSession,
    SnapshotItem,
    WaitingSession,
)
from app.modules.runs.session_transitions import invalid_transition, sources_of
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError

# Panggilan yang sudah diproses dan tidak diulang lagi (PBI-12 AC5).
_ATTEMPT_SELESAI = (
    AttemptStatus.SUCCEEDED,
    AttemptStatus.TECHNICAL_FAILURE,
)


def product_has_sessions(db: Session, product_id: uuid.UUID) -> bool:
    """Sesi yang diarsipkan tetap terhitung: datanya tidak pernah dihapus (AC10)."""
    return bool(db.scalar(select(exists().where(MeasurementSession.product_id == product_id))))


def session_progress(db: Session, measurement_id: uuid.UUID) -> Row | None:
    """Angka satu sesi untuk layar progres, atau None bila sesinya tidak ada.

    Dihitung lewat subquery supaya satu panggilan database cukup: endpoint ini
    di-poll tiap beberapa detik. Biaya dijumlahkan lewat attempt_id, bukan lewat
    call_logs.product_id, karena yang diminta biaya sesi ini saja.
    """
    jumlah_attempt = (
        select(func.count())
        .select_from(Attempt)
        .where(Attempt.session_id == MeasurementSession.id)
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    jumlah_selesai = (
        select(func.count())
        .select_from(Attempt)
        .where(
            Attempt.session_id == MeasurementSession.id,
            Attempt.status.in_(_ATTEMPT_SELESAI),
        )
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    jumlah_gagal = (
        select(func.count())
        .select_from(Attempt)
        .where(
            Attempt.session_id == MeasurementSession.id,
            Attempt.status == AttemptStatus.TECHNICAL_FAILURE,
        )
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    biaya = (
        select(func.coalesce(func.sum(CallLog.cost_idr), 0))
        .select_from(CallLog)
        .join(Attempt, Attempt.id == CallLog.attempt_id)
        .where(Attempt.session_id == MeasurementSession.id)
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    aktivitas_terakhir = (
        select(func.max(Attempt.updated_at))
        .select_from(Attempt)
        .where(Attempt.session_id == MeasurementSession.id)
        .correlate(MeasurementSession)
        .scalar_subquery()
    )
    return db.execute(
        select(
            MeasurementSession.product_id,
            MeasurementSession.started_at,
            MeasurementSession.updated_at,
            jumlah_attempt.label("total_calls"),
            jumlah_selesai.label("completed_calls"),
            jumlah_gagal.label("failed_calls"),
            biaya.label("running_cost_idr"),
            aktivitas_terakhir.label("last_attempt_at"),
        ).where(MeasurementSession.id == measurement_id)
    ).one_or_none()


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


class ExecutionReadRepository:
    """Read model worker/startup; persistence and status transitions retain their owners."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def running_automatic_id(self) -> uuid.UUID | None:
        return self._db.scalar(
            select(MeasurementSession.id).where(
                MeasurementSession.status == SessionStatus.RUNNING,
                MeasurementSession.session_type == SessionType.AUTOMATIC,
            )
        )

    def session(self, session_id: uuid.UUID) -> MeasurementSession:
        return self._db.scalars(
            select(MeasurementSession).where(MeasurementSession.id == session_id)
        ).one()

    def product_copy(self, session_id: uuid.UUID) -> SessionProductCopy:
        return self._db.scalars(
            select(SessionProductCopy).where(SessionProductCopy.session_id == session_id)
        ).one()

    def attempt_statuses(self, session_id: uuid.UUID) -> list[AttemptStatus]:
        return list(
            self._db.scalars(select(Attempt.status).where(Attempt.session_id == session_id))
        )

    def has_calls(self, session_id: uuid.UUID) -> bool:
        return (
            self._db.scalar(
                select(CallLog.id)
                .join(Attempt, Attempt.id == CallLog.attempt_id)
                .where(Attempt.session_id == session_id)
                .limit(1)
            )
            is not None
        )


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

    def archived_sessions(self) -> list[SessionRow]:
        """Sesi arsip, yang terakhir diarsipkan di atas (AC10)."""
        sessions = self._db.scalars(
            select(MeasurementSession)
            .where(MeasurementSession.archived_at.is_not(None))
            .order_by(MeasurementSession.archived_at.desc(), MeasurementSession.id)
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
            resumed_after_long_pause=session.resumed_after_long_pause,
            model_version_unverified=session.model_version_unverified,
            resume_error_code=session.resume_error_code,
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
                    id=attempt.id,
                    repetition_no=attempt.repetition_no,
                    status=AttemptStatus(attempt.status),
                    answer_text=attempt.answer_text,
                    model_version=attempt.model_version,
                    started_at=attempt.started_at,
                    finished_at=attempt.finished_at,
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
        terisi = Attempt.filled_at.is_not(None)
        rows = self._db.execute(
            select(Attempt.session_id, Attempt.status, terisi, func.count())
            .where(Attempt.session_id.in_(ids))
            .group_by(Attempt.session_id, Attempt.status, terisi)
        )
        counts: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for session_id, status, filled, count in rows:
            # PBI-22 D8: percobaan manual tetap pending; jawaban yang terisi (filled_at)
            # sudah punya hasil akhir, jadi dihitung selesai seperti succeeded.
            kunci = AttemptStatus.SUCCEEDED.value if filled else AttemptStatus(status).value
            counts[session_id][kunci] += count
        return {session_id: dict(per_status) for session_id, per_status in counts.items()}

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

    def next_try_no(self, attempt_id: uuid.UUID) -> int:
        """Nomor upaya berikutnya untuk attempt ini (D2 try_no, 1 = awal).

        Baca lalu tulis ini aman karena satu attempt hanya dipanggil satu dispatcher:
        lease advisory worker dan klaim attempt running sebelum panggilan (#98).
        UNIQUE (attempt_id, try_no) tetap menolak bila asumsi itu suatu saat dilanggar.
        """
        terakhir = self._db.scalar(
            select(func.max(CallLog.try_no)).where(CallLog.attempt_id == attempt_id)
        )
        return (terakhir or 0) + 1

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


_SESSION_SELESAI = frozenset(
    {
        SessionStatus.FINISHED,
        SessionStatus.FINISHED_WITH_FAILURES,
        SessionStatus.FAILED,
    }
)


def _kolom_waktu(
    target: SessionStatus, at: datetime, stop_reason: StopReason | None
) -> dict[str, object]:
    """Kolom waktu yang ikut berubah bersama status (D2)."""
    if target in (SessionStatus.RUNNING, SessionStatus.QUEUED):
        reopened: dict[str, object] = {"stopped_at": None, "stop_reason": None, "finished_at": None}
        if target is SessionStatus.QUEUED:
            reopened["queued_at"] = at
        return reopened
    kolom: dict[str, object] = {}
    if target in _SESSION_SELESAI:
        kolom["finished_at"] = at
    if stop_reason is not None:
        kolom["stopped_at"] = at
        kolom["stop_reason"] = stop_reason
    return kolom


def _catat_status(
    db: Session,
    session_id: uuid.UUID,
    asal: SessionStatus | str,
    tujuan: SessionStatus,
    stop_reason: StopReason | None,
) -> None:
    """Satu baris audit per perpindahan status, di transaksi yang sama (PBI-18, D6b).

    Listener audit hanya melihat flush ORM, sedangkan status ditulis lewat UPDATE Core
    bersyarat, jadi ketiga jalan tulis status memanggil ini sendiri. Pelaku dibaca dari
    session; tanpa pelaku (worker, antrean) kejadiannya tercatat sebagai sistem.
    """
    # Diimpor di sini: audit.service mengimpor runs.service, yang mengimpor file ini.
    from app.modules.audit import service as audit_service

    sesudah = {"status": tujuan.value}
    if stop_reason is not None:
        sesudah["stop_reason"] = stop_reason.value
    audit_service.record(
        db,
        action="measurement_session.status_changed",
        entity_type="measurement_session",
        entity_id=session_id,
        before={"status": SessionStatus(asal).value},
        after=sesudah,
    )


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
        commit: bool = True,
        finalized_by: uuid.UUID | None = None,
    ) -> None:
        target = SessionStatus(target)
        reason = None if stop_reason is None else StopReason(stop_reason)
        if target is SessionStatus.PAUSED and reason is None:
            raise ValidationError(
                "Sesi yang berhenti wajib punya penyebab (user, budget, atau restart).",
                code="STOP_REASON_REQUIRED",
                field="stop_reason",
            )
        # D9: Selesai sesi manual (PBI-22 AC8) selalu tercatat bersama pelakunya.
        selesai: dict[str, object] = {}
        if target is SessionStatus.FINALIZED:
            if finalized_by is None:
                raise ValidationError(
                    "Sesi manual yang ditandai selesai wajib mencatat pelakunya.",
                    code="FINALIZED_BY_REQUIRED",
                    field="finalized_by",
                )
            selesai = {"finalized_at": at, "finalized_by": finalized_by}
        # Kunci baris dulu supaya status asal di catatan audit pasti status yang diganti.
        asal = self._db.scalar(
            select(MeasurementSession.status)
            .where(MeasurementSession.id == session_id)
            .with_for_update()
        )
        diubah = self._db.execute(
            update(MeasurementSession)
            .where(
                MeasurementSession.id == session_id,
                MeasurementSession.status.in_(sources_of(target)),
                # Diterima apa adanya = selesai sepenuhnya (AC9): tidak berpindah ke mana pun.
                MeasurementSession.accepted_as_is_at.is_(None),
            )
            .values(status=target, **_kolom_waktu(target, at, reason), **selesai)
            .execution_options(synchronize_session=False)
        ).rowcount
        if diubah == 1:
            try:
                _catat_status(self._db, session_id, asal, target, reason)
                if commit:
                    self._db.commit()
                else:
                    self._db.flush()
            except Exception:
                self._db.rollback()
                raise
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


def attempt_statuses(db: Session, session_id: uuid.UUID) -> dict[uuid.UUID, AttemptStatus]:
    """Status setiap percobaan sesi, urut kasus lalu nomor pengulangan (SCRUM-218)."""
    rows = db.execute(
        select(Attempt.id, Attempt.status)
        .where(Attempt.session_id == session_id)
        .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
    )
    return dict(rows.all())


def session_status(db: Session, session_id: uuid.UUID) -> SessionStatus | None:
    return db.scalar(select(MeasurementSession.status).where(MeasurementSession.id == session_id))


def attempt_with_calls(
    db: Session, session_id: uuid.UUID, attempt_id: uuid.UUID
) -> tuple[Attempt, dict, list[CallLog]] | None:
    """Percobaan sesi ini, body item snapshot-nya, dan semua upayanya urut try_no."""
    baris = db.execute(
        select(Attempt, CaseSnapshotItem.body)
        .join(CaseSnapshotItem, CaseSnapshotItem.id == Attempt.snapshot_item_id)
        .where(Attempt.id == attempt_id, Attempt.session_id == session_id)
    ).first()
    if baris is None:
        return None
    attempt, body = baris
    calls = list(
        db.scalars(select(CallLog).where(CallLog.attempt_id == attempt_id).order_by(CallLog.try_no))
    )
    return attempt, body, calls


def reopen_for_retry(
    db: Session, session_id: uuid.UUID, attempt_ids: Sequence[uuid.UUID], *, at: datetime
) -> None:
    """Satu transaksi: percobaan gagal kembali pending dan sesi berjalan lagi (PBI-12 AC3, D4).

    Status sesi lewat change_status (SCRUM-214). Bila sesi otomatis lain sedang
    running, partial index #89 menolak dan percobaan ikut dibatalkan.
    Waktu mulai dan selesai dikosongkan; riwayat tiap upaya tetap ada di call_logs.
    """
    try:
        db.execute(
            update(Attempt)
            .where(
                Attempt.session_id == session_id,
                Attempt.id.in_(attempt_ids),
                Attempt.status == AttemptStatus.TECHNICAL_FAILURE,
            )
            .values(status=AttemptStatus.PENDING, started_at=None, finished_at=None)
            .execution_options(synchronize_session=False)
        )
        SessionStatusRepository(db).change_status(
            session_id, SessionStatus.RUNNING, at=at, commit=False
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _sesi_lain_running(exc):
            raise ConflictError(
                "Masih ada pengukuran otomatis lain yang berjalan. Coba ulang setelah selesai.",
                code="ANOTHER_SESSION_RUNNING",
            ) from exc
        raise


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
        prepared = bool(
            self._db.scalar(select(exists().where(SessionProductCopy.session_id == session.id)))
        )
        return WaitingSession(
            id=session.id,
            prepared=prepared,
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
            _freeze(self._db, session_id, items, product, AttemptSource.AUTOMATIC)
            _catat_status(self._db, session_id, SessionStatus.QUEUED, SessionStatus.RUNNING, None)
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            if isinstance(exc, IntegrityError) and _sesi_lain_running(exc):
                return False
            raise
        return True

    def reopen(self, session_id: uuid.UUID, *, at: datetime) -> bool:
        """Sesi Menunggu hasil Lanjutkan kembali running tanpa snapshot baru (SCRUM-267).

        UPDATE bersyarat seperti start(): hanya dari queued, jadi sesi yang sempat
        dibatalkan tidak ikut terbuka. started_at awal tetap; at menjadi heartbeat awal,
        supaya cek liveness tidak menganggap sesi yang lama menunggu sebagai worker mati.
        """
        try:
            dibuka = self._db.execute(
                update(MeasurementSession)
                .where(MeasurementSession.id == session_id, *_ANTREAN_OTOMATIS)
                .values(status=SessionStatus.RUNNING, updated_at=at)
                .execution_options(synchronize_session=False)
            ).rowcount
            if dibuka != 1:
                self._db.rollback()
                return False
            _catat_status(self._db, session_id, SessionStatus.QUEUED, SessionStatus.RUNNING, None)
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
        try:
            if gagal == 1:
                _catat_status(
                    self._db,
                    session_id,
                    SessionStatus.QUEUED,
                    SessionStatus.FAILED,
                    StopReason.ERROR,
                )
            self._db.commit()
        except Exception:
            # Sama seperti start() dan change_status: session worker harus tetap bisa dipakai.
            self._db.rollback()
            raise
        return gagal == 1


def _manual_attempt_row(row: Attempt) -> ManualAttemptRow:
    """Satu bentuk baris percobaan manual untuk daftar maupun satu slot (D9)."""
    return ManualAttemptRow(
        id=row.id,
        snapshot_item_id=row.snapshot_item_id,
        repetition_no=row.repetition_no,
        filled=row.filled_at is not None,
        answered_at_platform=row.answered_at_platform,
        source=row.source,
        answer_md=row.answer_text,
        platform_refused=row.platform_refused,
        note=row.manual_note,
        filled_by=row.filled_by,
        filled_at=row.filled_at,
    )


class ManualSessionRepository:
    """Sesi manual di tabel D2 (PBI-22 SCRUM-230).

    Memenuhi port ManualSessionStore (membuat sesi) dan ManualStatusStore (status, Selesai,
    halaman sesi), supaya service tidak menulis query sendiri (review #116).
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_manual(
        self, new: NewSession, *, items: Sequence[SnapshotItem], product: ProductCopy
    ) -> uuid.UUID:
        """D8 "Admin membuat sesi": semua tersimpan dalam satu transaksi, atau tidak sama sekali."""
        try:
            session = MeasurementSession(
                label=new.label,
                purpose=SessionPurpose(new.purpose.value),
                session_type=SessionType.MANUAL,
                product_id=new.product_id,
                repetitions=new.repetitions,
                split_filter=SplitFilter(new.case_filter.value),
                status=SessionStatus.NOT_FILLED,
                created_by=new.created_by,
                judge_provider_id=new.judge_provider_id,
            )
            self._db.add(session)
            self._db.flush()
            self._db.add_all(
                MeasurementSessionSuite(session_id=session.id, suite_id=suite_id)
                for suite_id in new.suite_ids
            )
            _freeze(self._db, session.id, items, product, AttemptSource.MANUAL)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return session.id

    def lock_manual(self, session_id: uuid.UUID) -> SessionStatus | None:
        session = get_session_for_update(self._db, session_id)
        if session is None or session.session_type != SessionType.MANUAL:
            return None
        return SessionStatus(session.status)

    def manual_attempts(self, session_id: uuid.UUID) -> list[ManualAttemptRow]:
        rows = self._db.scalars(
            select(Attempt)
            .where(Attempt.session_id == session_id)
            .order_by(Attempt.snapshot_item_id, Attempt.repetition_no)
        ).all()

        return [_manual_attempt_row(row) for row in rows]

    def is_manual_session(self, session_id: uuid.UUID) -> bool:
        """Sesi ada dan bertipe manual; dipakai riwayat untuk menjawab 404 (AC5)."""
        return (
            self._db.scalar(
                select(MeasurementSession.session_type).where(MeasurementSession.id == session_id)
            )
            == SessionType.MANUAL
        )

    def answer_slot(
        self,
        session_id: uuid.UUID,
        snapshot_item_id: uuid.UUID,
        attempt_no: int,
        *,
        lock: bool = False,
    ) -> ManualAttemptRow | None:
        query = select(Attempt).where(
            Attempt.session_id == session_id,
            Attempt.snapshot_item_id == snapshot_item_id,
            Attempt.repetition_no == attempt_no,
            Attempt.source == AttemptSource.MANUAL,
        )
        if lock:
            query = query.with_for_update()
        row = self._db.scalar(query)
        return _manual_attempt_row(row) if row is not None else None

    def case_attempt_count(self, session_id: uuid.UUID, snapshot_item_id: uuid.UUID) -> int:
        return (
            self._db.scalar(
                select(func.count())
                .select_from(Attempt)
                .where(
                    Attempt.session_id == session_id,
                    Attempt.snapshot_item_id == snapshot_item_id,
                    Attempt.source == AttemptSource.MANUAL,
                )
            )
            or 0
        )

    def archive_answer(
        self,
        attempt_id: uuid.UUID,
        *,
        overwritten_by: uuid.UUID,
        overwritten_at: datetime,
    ) -> None:
        """Salin jawaban yang sekarang ke answer_history, tanpa menyentuh baris lama (AC5)."""
        row = self._db.get(Attempt, attempt_id)
        if row is None:
            return
        self._db.add(
            AnswerHistory(
                attempt_id=row.id,
                answer_md=row.answer_text,
                filled_by=row.filled_by,
                filled_at=row.filled_at,
                obtained_at=row.answered_at_platform,
                platform_refused=row.platform_refused,
                note=row.manual_note,
                overwritten_by=overwritten_by,
                overwritten_at=overwritten_at,
            )
        )
        self._db.flush()

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
        row = self._db.get(Attempt, attempt_id)
        if row is None:
            raise NotFoundError("Percobaan tidak ditemukan.", code="MANUAL_ATTEMPT_NOT_FOUND")
        row.answer_text = answer_md
        row.platform_refused = platform_refused
        row.manual_note = note
        row.answered_at_platform = obtained_at
        row.filled_by = filled_by
        row.filled_at = filled_at
        self._db.flush()
        return _manual_attempt_row(row)

    def answer_history(self, attempt_id: uuid.UUID) -> list[ManualAnswerHistoryRow]:
        rows = self._db.scalars(
            select(AnswerHistory)
            .where(AnswerHistory.attempt_id == attempt_id)
            .order_by(AnswerHistory.overwritten_at, AnswerHistory.id)
        ).all()
        return [
            ManualAnswerHistoryRow(
                id=row.id,
                attempt_id=row.attempt_id,
                answer_md=row.answer_md,
                filled_by=row.filled_by,
                filled_at=row.filled_at,
                obtained_at=row.obtained_at,
                platform_refused=row.platform_refused,
                note=row.note,
                overwritten_by=row.overwritten_by,
                overwritten_at=row.overwritten_at,
            )
            for row in rows
        ]

    def commit(self) -> None:
        self._db.commit()

    def rollback(self) -> None:
        self._db.rollback()

    def set_answer_dates(
        self, session_id: uuid.UUID, start: datetime | None, end: datetime | None
    ) -> None:
        self._db.execute(
            update(MeasurementSession)
            .where(MeasurementSession.id == session_id)
            .values(answer_date_from=start, answer_date_to=end)
            .execution_options(synchronize_session=False)
        )

    def change_status(
        self,
        session_id: uuid.UUID,
        target: SessionStatus,
        *,
        at: datetime,
        finalized_by: uuid.UUID | None = None,
        commit: bool,
    ) -> None:
        SessionStatusRepository(self._db).change_status(
            session_id, target, at=at, finalized_by=finalized_by, commit=commit
        )

    def read_manual(self, session_id: uuid.UUID) -> ManualSessionRow | None:
        session = self._db.get(MeasurementSession, session_id)
        if session is None or session.session_type != SessionType.MANUAL:
            return None
        # Baris manual lama tanpa salinan produk tetap terbaca, bukan 500 (review #116).
        product = self._db.scalars(
            select(SessionProductCopy).where(SessionProductCopy.session_id == session_id)
        ).first()
        return ManualSessionRow(
            id=session.id,
            label=session.label,
            purpose=SessionPurpose(session.purpose).value,
            status=SessionStatus(session.status),
            case_filter=SplitFilter(session.split_filter).value,
            repetitions=session.repetitions,
            created_by=session.created_by,
            created_at=session.created_at,
            answer_date_from=session.answer_date_from,
            answer_date_to=session.answer_date_to,
            finalized_at=session.finalized_at,
            finalized_by=session.finalized_by,
            product_name=product.name if product else "",
            vendor_name=product.vendor_name if product else None,
            site_url=product.site_url if product else None,
            manual_params=product.manual_params if product else None,
        )

    def snapshot_cases(self, session_id: uuid.UUID) -> list[ManualCaseRow]:
        rows = self._db.execute(
            select(CaseSnapshotItem.id, CaseSnapshotItem.case_id, CaseSnapshotItem.body)
            .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
            .where(CaseSnapshot.session_id == session_id)
        )
        return [
            ManualCaseRow(snapshot_item_id=item_id, case_id=case_id, body=body)
            for item_id, case_id, body in rows
        ]


def _freeze(
    db: Session,
    session_id: uuid.UUID,
    items: Sequence[SnapshotItem],
    product: ProductCopy,
    source: AttemptSource,
) -> None:
    """Snapshot, salinan produk, dan percobaan pending (D3 langkah 26-28, D8 sesi manual)."""
    snapshot = CaseSnapshot(session_id=session_id)
    db.add(snapshot)
    db.flush()
    rows = [
        CaseSnapshotItem(
            snapshot_id=snapshot.id,
            case_id=item.case_id,
            case_version_id=item.case_version_id,
            body=item.content,
        )
        for item in items
    ]
    db.add_all(rows)
    db.add(_product_copy_row(session_id, product))
    db.flush()
    repetitions = db.scalar(
        select(MeasurementSession.repetitions).where(MeasurementSession.id == session_id)
    )
    db.add_all(
        Attempt(
            session_id=session_id,
            snapshot_item_id=row.id,
            repetition_no=nomor,
            source=source,
            status=AttemptStatus.PENDING,
        )
        for row in rows
        for nomor in range(1, repetitions + 1)
    )
    db.flush()


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
        vendor_name=product.vendor_name,
        site_url=product.site_url,
        manual_params=product.manual_params,
    )


def snapshot_case_items(db: Session, session_id: uuid.UUID) -> list[tuple[uuid.UUID, dict]]:
    """Id dan body tiap item snapshot sesi, urut id supaya hasilnya stabil (PBI-14 AC10)."""
    baris = db.execute(
        select(CaseSnapshotItem.id, CaseSnapshotItem.body)
        .join(CaseSnapshot, CaseSnapshot.id == CaseSnapshotItem.snapshot_id)
        .where(CaseSnapshot.session_id == session_id)
        .order_by(CaseSnapshotItem.id)
    ).all()
    return [(item_id, body or {}) for item_id, body in baris]


def session_attempt_items(
    db: Session,
    session_id: uuid.UUID,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Map attempt IDs to frozen case snapshot item IDs."""
    rows = db.execute(
        select(Attempt.id, Attempt.snapshot_item_id)
        .where(Attempt.session_id == session_id)
        .order_by(Attempt.snapshot_item_id, Attempt.repetition_no, Attempt.id)
    ).all()
    return list(rows)
