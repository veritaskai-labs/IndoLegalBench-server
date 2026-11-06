"""SCRUM-225 AC7 PostgreSQL regrade integration tests."""

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunStatus,
    JudgeKind,
)
from app.modules.grading.regrade_sql import (
    JudgeCriterion,
    JudgeOutcome,
    append_sql_judge_failure,
    append_sql_judgement,
    finish_sql_regrade,
    mark_sql_regrade_failed,
    start_sql_regrade,
)
from app.modules.grading.service import (
    read_latest_grades,
    read_run_history,
)
from tests.grading_pg import (
    aktifkan,
    buat_baris_juri,
    buat_hasil,
    buat_percobaan,
    buat_rubrik_lengkap,
    buat_run,
    buat_versi_aktif,
    jalankan_run,
    selesaikan_run,
    ubah_run,
)
from tests.measurement_pg import (
    buat_admin,
    buat_produk,
    buat_sesi,
)

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_POSTGRES_URL,
        reason="TEST_POSTGRES_URL not set",
    ),
]


@pytest.fixture
def pg():
    engine = create_engine(TEST_POSTGRES_URL)

    with engine.connect() as conn:
        transaction = conn.begin()

        try:
            with Session(
                bind=conn,
                join_transaction_mode="create_savepoint",
            ) as db:
                yield db, conn
        finally:
            transaction.rollback()

    engine.dispose()


def old_published(conn, additional_quality_score=None):
    user_id = buat_admin(conn)
    product_id = buat_produk(conn, user_id)
    session_id = buat_sesi(conn, product_id, user_id)
    rubric_id = buat_versi_aktif(conn, user_id)
    attempt_id = buat_percobaan(conn, session_id, user_id)

    run_id = buat_run(conn, session_id, rubric_id, user_id)

    jalankan_run(conn, run_id)

    result_id = buat_hasil(conn, run_id, attempt_id)

    buat_baris_juri(
        conn,
        result_id,
        kind="quality",
        judge_run_no=1,
    )

    buat_baris_juri(
        conn,
        result_id,
        kind="format",
        judge_run_no=1,
    )

    if additional_quality_score is not None:
        buat_baris_juri(
            conn,
            result_id,
            kind="quality",
            judge_run_no=2,
            criteria_met=int(additional_quality_score * 4),
            criteria_total=4,
            criteria_score=additional_quality_score,
        )

    selesaikan_run(conn, run_id)
    ubah_run(conn, run_id, is_latest=True)

    return (
        user_id,
        session_id,
        rubric_id,
        attempt_id,
        run_id,
        result_id,
    )


def activate_v2(conn, user_id, old_rubric):
    conn.execute(
        text("UPDATE rubric_versions SET is_active = false WHERE id = :id"),
        {"id": old_rubric},
    )

    new_rubric = buat_rubrik_lengkap(
        conn,
        user_id,
        version_no=2,
    )

    aktifkan(conn, new_rubric)

    return new_rubric


def outcome(attempt_id, kind, met):
    return JudgeOutcome(
        attempt_id=attempt_id,
        kind=kind,
        judge_run_no=1,
        prompt="Synthetic test judge prompt",
        criteria=(
            JudgeCriterion(
                "must_contain:0",
                "Criterion A",
                met,
            ),
            JudgeCriterion(
                "must_contain:1",
                "Criterion B",
                True,
            ),
        ),
    )


def test_sql_regrade_preserves_history_and_deterministic(pg):
    db, conn = pg

    user, session, old_version, attempt, old_run, _ = old_published(conn)

    new_version = activate_v2(conn, user, old_version)

    new_run = start_sql_regrade(db, session, user)

    assert new_run is not None
    assert db.get(GradingRun, new_run).status == (GradingRunStatus.RUNNING)

    copied = db.scalar(select(GradingResult).where(GradingResult.grading_run_id == new_run))

    assert copied.det_score == Decimal("0.75")

    append_sql_judgement(
        db,
        new_run,
        outcome(attempt, JudgeKind.QUALITY, False),
    )

    append_sql_judgement(
        db,
        new_run,
        outcome(attempt, JudgeKind.FORMAT, True),
    )

    finish_sql_regrade(db, session, new_run)

    assert db.get(GradingRun, old_run).is_latest is False
    assert db.get(GradingRun, new_run).is_latest is True

    assert db.get(GradingRun, old_run).rubric_version_id == old_version

    assert db.get(GradingRun, new_run).rubric_version_id == new_version

    count = db.scalar(
        select(func.count())
        .select_from(GradingResult)
        .where(GradingResult.grading_run_id.in_((old_run, new_run)))
    )

    assert count == 2

    latest = read_latest_grades(db, session)

    assert latest.deterministic[attempt] == Decimal("0.75")
    assert latest.quality[attempt] == Decimal("0.50")
    assert latest.format[attempt] == Decimal("1.00")

    history = read_run_history(db, session)

    assert len(history) == 2
    assert [row.is_latest for row in history] == [False, True]


def test_same_active_rubric_does_not_regrade(pg):
    db, conn = pg

    user, session, _, _, _, _ = old_published(conn)

    assert start_sql_regrade(db, session, user) is None

    count = db.scalar(select(func.count()).select_from(GradingRun))

    assert count == 1


def test_missing_format_blocks_publication(pg):
    db, conn = pg

    user, session, old_version, attempt, old_run, _ = old_published(conn)

    activate_v2(conn, user, old_version)

    new_run = start_sql_regrade(db, session, user)

    append_sql_judgement(
        db,
        new_run,
        outcome(attempt, JudgeKind.QUALITY, True),
    )

    with pytest.raises(ValueError, match="incomplete"):
        finish_sql_regrade(db, session, new_run)

    assert db.get(GradingRun, old_run).is_latest is True
    assert db.get(GradingRun, new_run).status == (GradingRunStatus.RUNNING)

    mark_sql_regrade_failed(db, session, new_run)

    assert db.get(GradingRun, old_run).is_latest is True
    assert db.get(GradingRun, new_run).status == (GradingRunStatus.FAILED)


def test_failed_judge_keeps_old_latest(pg):
    db, conn = pg

    user, session, old_version, attempt, old_run, _ = old_published(conn)

    activate_v2(conn, user, old_version)

    new_run = start_sql_regrade(db, session, user)

    append_sql_judgement(
        db,
        new_run,
        outcome(attempt, JudgeKind.QUALITY, True),
    )

    append_sql_judge_failure(
        db,
        new_run,
        attempt,
        JudgeKind.FORMAT,
        1,
        "Synthetic prompt",
    )

    with pytest.raises(ValueError, match="incomplete"):
        finish_sql_regrade(db, session, new_run)

    mark_sql_regrade_failed(db, session, new_run)

    assert db.get(GradingRun, old_run).is_latest is True

    latest = read_latest_grades(db, session)

    assert latest.quality[attempt] == Decimal("0.75")


def test_duplicate_running_regrade_rejected(pg):
    db, conn = pg

    user, session, old_version, _, _, _ = old_published(conn)

    activate_v2(conn, user, old_version)

    first_run = start_sql_regrade(db, session, user)

    assert first_run is not None

    with pytest.raises(
        ValueError,
        match="already in progress",
    ):
        start_sql_regrade(db, session, user)


def test_failed_regrade_can_be_retried(pg):
    db, conn = pg

    user, session, old_version, _, old_run, _ = old_published(conn)

    activate_v2(conn, user, old_version)

    failed_run = start_sql_regrade(db, session, user)

    mark_sql_regrade_failed(db, session, failed_run)

    retried = start_sql_regrade(db, session, user)

    assert retried is not None
    assert retried != failed_run

    assert db.get(GradingRun, old_run).is_latest is True


def test_multiple_successful_judges_are_averaged(pg):
    db, conn = pg

    _, session, _, attempt, _, _ = old_published(
        conn,
        additional_quality_score=Decimal("0.25"),
    )

    latest = read_latest_grades(db, session)

    assert latest.quality[attempt] == Decimal("0.50")
