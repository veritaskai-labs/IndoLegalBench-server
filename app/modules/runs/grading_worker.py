"""Worker penilaian di latar belakang. SCRUM-78 SCRUM-284 bagian 2 (K-13).

Mengambil run queued yang dicatat POST /measurements/{id}/grading-runs, memanggil judge per
percobaan sambil mencatat progres dan heartbeat, lalu menyimpan hasil ke run yang sama dengan
aturan yang sama seperti POST /grade (hasil terbaru, run gagal, status Dinilai sesi manual).

Run yang heartbeat-nya basi ditandai gagal GRADING_INTERRUPTED dan tidak dilanjutkan otomatis:
panggilan judge yang sudah terkirim mungkin sudah ditagih (pola sama dengan SCRUM-268), jadi
Admin yang memutuskan menilai ulang.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.grading.models import (
    GradingRun,
    GradingRunProgress,
    GradingRunStatus,
    RubricVersion,
)
from app.modules.runs.exchange_rate import ExchangeRateProvider
from app.modules.runs.manual_grade_gate import ManualGradeGate
from app.modules.runs.models import MeasurementSession
from app.modules.runs.repository import ManualSessionRepository
from app.modules.runs.session_grade import (
    RUN_COMPLETED,
    DbGradingStore,
    ensure_unchanged_since_load,
    execute_judge_for_all_cases,
    grade_session_data,
    load_case_grade_inputs,
    open_judge_meter_or_error,
    resolve_active_judge_asker,
)
from app.shared.exceptions import ConflictError

logger = logging.getLogger(__name__)

NO_SCORE = "NO_SCORE"
GRADING_ERROR = "GRADING_ERROR"
GRADING_INTERRUPTED = "GRADING_INTERRUPTED"
# Satu panggilan judge dibatasi 240 detik (ASK_TIMEOUT_SECONDS); heartbeat diperbarui tiap
# percobaan, jadi 300 detik tanpa heartbeat berarti worker sudah mati.
STALE_AFTER_SECONDS = 300


def claim_next_run(db: Session, *, now: datetime) -> uuid.UUID | None:
    """Ambil run queued paling lama dan tandai running; None bila antrean kosong.

    FOR UPDATE SKIP LOCKED di PostgreSQL, jadi dua worker tidak mengambil run yang sama.
    """
    run = db.scalars(
        select(GradingRun)
        .where(GradingRun.status == GradingRunStatus.QUEUED)
        .order_by(GradingRun.triggered_at.asc(), GradingRun.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if run is None:
        db.rollback()
        return None
    run.status = GradingRunStatus.RUNNING
    progress = db.get(GradingRunProgress, run.id)
    progress.started_at = now
    progress.heartbeat_at = now
    db.commit()
    return run.id


def execute_run(
    db: Session,
    run_id: uuid.UUID,
    *,
    now: datetime | None = None,
    rates: ExchangeRateProvider | None = None,
) -> None:
    """Nilai satu run running; galat apa pun mengakhiri run sebagai failed dengan kodenya."""
    clock = _clock(now)
    try:
        _grade(db, run_id, clock, rates)
    except ConflictError as exc:
        # Jawaban berubah atau sesi diarsipkan selama judge berjalan: tidak ada hasil tersimpan.
        db.rollback()
        _fail(db, run_id, exc.code or GRADING_ERROR, clock())
    except Exception as exc:
        db.rollback()
        # Hanya jenis galat: pesan provider atau koneksi bisa memuat rahasia.
        logger.error("Grading run %s failed (%s)", run_id, type(exc).__name__)
        _fail(db, run_id, GRADING_ERROR, clock())


def fail_stale_runs(
    db: Session, *, now: datetime, timeout_seconds: float = STALE_AFTER_SECONDS
) -> list[uuid.UUID]:
    """Run running tanpa heartbeat selama timeout_seconds ditandai gagal GRADING_INTERRUPTED."""
    cutoff = now - timedelta(seconds=timeout_seconds)
    rows = db.execute(
        select(GradingRun, GradingRunProgress)
        .join(GradingRunProgress, GradingRunProgress.grading_run_id == GradingRun.id)
        .where(GradingRun.status == GradingRunStatus.RUNNING)
    ).all()
    stale = [run.id for run, progress in rows if _as_utc(progress.heartbeat_at) <= cutoff]
    for run_id in stale:
        _fail(db, run_id, GRADING_INTERRUPTED, now)
    return stale


def run_once(
    db: Session, *, now: datetime | None = None, rates: ExchangeRateProvider | None = None
) -> bool:
    """Satu putaran worker: ambil dan nilai satu run. True bila ada run yang dikerjakan."""
    run_id = claim_next_run(db, now=_clock(now)())
    if run_id is None:
        return False
    execute_run(db, run_id, now=now, rates=rates)
    return True


def _grade(
    db: Session,
    run_id: uuid.UUID,
    clock: Callable[[], datetime],
    rates: ExchangeRateProvider | None,
) -> None:
    run = db.get(GradingRun, run_id)
    progress = db.get(GradingRunProgress, run_id)
    session = db.get(MeasurementSession, run.session_id)
    cases = load_case_grade_inputs(db, session.id)

    asker, judge_error, binding = resolve_active_judge_asker(
        db, session, None, run.judge_provider_id
    )
    meter, pricing_error = open_judge_meter_or_error(db, binding, rates, clock())
    if pricing_error is not None:
        asker, judge_error = None, pricing_error

    def tick() -> None:
        progress.attempts_graded = min(progress.attempts_graded + 1, progress.attempts_total)
        progress.heartbeat_at = clock()
        db.commit()

    execute_judge_for_all_cases(asker, judge_error, cases, meter, on_attempt=tick)
    progress.attempts_graded = progress.attempts_total

    store = DbGradingStore(db)
    response = grade_session_data(
        session_id=session.id,
        session_label=session.label,
        cases_input=cases,
        triggered_by=run.triggered_by,
        rubric_version=_rubric_label(db, run.rubric_version_id),
        existing_runs=store.get_grading_runs(session.id),
        now=run.triggered_at,
    ).model_copy(update={"grading_run_id": run.id, "kind": run.kind.value})

    ensure_unchanged_since_load(db, session.id, cases)
    finished_at = clock()
    store.complete_grading_run(run, response, cases, finished_at=finished_at)
    if response.status == RUN_COMPLETED:
        # D8: sesi manual Lengkap -> Dinilai di transaksi yang sama dengan hasilnya.
        ManualGradeGate(ManualSessionRepository(db)).after_grading(session.id, now=finished_at)
    else:
        progress.error_code = NO_SCORE
    db.commit()


def _fail(db: Session, run_id: uuid.UUID, code: str, at: datetime) -> None:
    run = db.get(GradingRun, run_id)
    run.status = GradingRunStatus.FAILED
    run.finished_at = at
    db.get(GradingRunProgress, run_id).error_code = code
    db.commit()


def _rubric_label(db: Session, rubric_version_id: uuid.UUID) -> str:
    return f"v{db.get(RubricVersion, rubric_version_id).version_no}"


def _clock(now: datetime | None) -> Callable[[], datetime]:
    """Waktu tetap untuk tes; tanpa itu jam sungguhan, supaya heartbeat run panjang tetap baru."""
    if now is not None:
        return lambda: now
    return lambda: datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """SQLite mengembalikan waktu tanpa zona; semua waktu di sini disimpan dalam UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
