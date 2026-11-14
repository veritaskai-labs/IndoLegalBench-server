"""SCRUM-284: worker penilaian melewati trigger PostgreSQL grading_runs dan grading_results.

Alur queued -> running -> finished harus diterima grading_runs_guard, dan hasil per percobaan
ditulis saat run masih running (grading_results hanya menerima run yang belum berakhir).
Judge tiruan disuntik lewat resolve_active_judge_asker milik worker.

Dilewati kalau TEST_POSTGRES_URL kosong.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunProgress,
    GradingRunStatus,
)
from app.modules.runs import grading_worker
from app.modules.runs.grading_queue import enqueue_grading
from app.shared.security import Role
from tests.integration.test_grade_pg import (  # noqa: F401  (fixture)
    _penilai_tiruan,
    _setup_pg_grade_session,
    pg,
    pytestmark,
)

T0 = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def test_worker_menilai_run_antre_sampai_selesai(pg, monkeypatch):  # noqa: F811
    """Positif: K-13, run antre dinilai worker dan menjadi hasil terbaru di PostgreSQL."""
    # Arrange
    db, conn = pg
    admin_id, session_id, _ = _setup_pg_grade_session(conn)
    monkeypatch.setattr(
        grading_worker,
        "resolve_active_judge_asker",
        lambda db, session, judge_asker, judge_id=None: (_penilai_tiruan(), None, None),
    )
    queued = enqueue_grading(db, session_id, None, actor_id=admin_id, actor_role=Role.ADMIN, now=T0)

    # Act
    sibuk = grading_worker.run_once(db, now=T0)

    # Assert
    db.expire_all()
    run = db.get(GradingRun, queued.grading_run_id)
    progres = db.get(GradingRunProgress, run.id)
    hasil = db.scalar(
        select(func.count())
        .select_from(GradingResult)
        .where(GradingResult.grading_run_id == run.id)
    )
    assert sibuk is True
    assert run.status == GradingRunStatus.FINISHED
    assert run.is_latest is True
    assert progres.attempts_graded == progres.attempts_total
    assert hasil > 0
