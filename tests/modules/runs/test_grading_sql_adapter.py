"""SCRUM-225: real grading tables and read-time score reports."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.auth.models import User
from app.modules.grading.models import (
    GradingResult,
    GradingRun,
    GradingRunKind,
    GradingRunStatus,
    JudgeKind,
    JudgeStatus,
    LlmJudgeResult,
)
from app.modules.grading.models import (
    RubricVersion as DbRubricVersion,
)
from app.modules.grading.run_service import (
    active_rubric_version_no,
    publish_latest_run,
    read_latest_grades,
    read_run_history,
)
from app.modules.providers.models import AiProduct, MeasurementType
from app.modules.runs.adapters import SqlGradingReader
from app.modules.runs.models import (
    Attempt,
    AttemptSource,
    CaseSnapshot,
    CaseSnapshotItem,
    MeasurementSession,
    SessionPurpose,
    SessionType,
    SplitFilter,
)
from app.modules.runs.score_report import read_session_report
from app.shared.security import Role

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def make_result(db, run_id, attempt_id, det_score):
    result = GradingResult(
        grading_run_id=run_id,
        attempt_id=attempt_id,
        is_technical_disruption=False,
        det_score=Decimal(det_score),
        det_regulation_matches=[],
        det_article_matches=[],
        det_ungradable_refs=[],
        det_all_chained_cited=True,
        det_reference_total=4,
        det_reference_correct=3,
        det_extracted_total=4,
        det_extracted_correct=3,
        det_formula="recall",
    )
    db.add(result)
    db.flush()
    return result


def make_judge(db, result_id, kind, met):
    db.add(
        LlmJudgeResult(
            grading_result_id=result_id,
            judge_run_no=1,
            kind=kind,
            status=JudgeStatus.SUCCEEDED,
            prompt="Synthetic judge prompt",
            criteria_met=met,
            criteria_total=4,
            criteria_score=Decimal(met) / Decimal(4),
        )
    )
    db.flush()


@pytest.fixture
def grading_data(db_session):
    db = db_session

    user = User(
        email="grading-sql-225@test.local",
        name="Admin",
        role=Role.ADMIN,
    )
    db.add(user)
    db.flush()

    product = AiProduct(
        name="Grading test product",
        created_by=user.id,
        measurement_type=MeasurementType.MANUAL,
        vendor_name="Test",
        site_url="https://grading.test",
    )
    db.add(product)
    db.flush()

    session = MeasurementSession(
        label="Grading SQL integration",
        purpose=SessionPurpose.TRIAL,
        session_type=SessionType.AUTOMATIC,
        product_id=product.id,
        repetitions=1,
        split_filter=SplitFilter.BOTH,
        created_by=user.id,
    )
    db.add(session)
    db.flush()

    snapshot = CaseSnapshot(session_id=session.id)
    db.add(snapshot)
    db.flush()

    item = CaseSnapshotItem(
        snapshot_id=snapshot.id,
        case_id=uuid.uuid4(),
        case_version_id=uuid.uuid4(),
        body={
            "version_no": 1,
            "sections": {
                "case_code": "CASE-225",
                "split_tag": "dev",
                "category": None,
            },
        },
    )
    db.add(item)
    db.flush()

    attempt = Attempt(
        session_id=session.id,
        snapshot_item_id=item.id,
        repetition_no=1,
        source=AttemptSource.AUTOMATIC,
    )
    db.add(attempt)
    db.flush()

    rubric = DbRubricVersion(
        version_no=1,
        effective_date=date(2026, 11, 6),
        is_active=True,
        locked_at=NOW,
        judge_prompt_quality="Synthetic quality prompt",
        judge_prompt_format="Synthetic format prompt",
        created_by=user.id,
    )
    db.add(rubric)
    db.flush()

    run = GradingRun(
        session_id=session.id,
        rubric_version_id=rubric.id,
        kind=GradingRunKind.INITIAL,
        status=GradingRunStatus.FINISHED,
        finished_at=NOW,
        is_latest=True,
        triggered_by=user.id,
    )
    db.add(run)
    db.flush()

    result = make_result(db, run.id, attempt.id, "0.75")
    make_judge(db, result.id, JudgeKind.QUALITY, 3)
    make_judge(db, result.id, JudgeKind.FORMAT, 1)

    db.flush()
    return session, attempt, user, rubric, run


def test_read_sql_scores_remain_separate(db_session, grading_data):
    session, attempt, _, rubric, run = grading_data

    stored = read_latest_grades(db_session, session.id)

    assert stored.run_id == run.id
    assert stored.rubric_version_id == rubric.id
    assert stored.deterministic[attempt.id] == Decimal("0.75")
    assert stored.quality[attempt.id] == Decimal("0.75")
    assert stored.format[attempt.id] == Decimal("0.25")

    report = read_session_report(
        db_session,
        session.id,
        SqlGradingReader(db_session),
    )

    assert report.quality.totals.dev.deterministic == Decimal("0.75")
    assert report.quality.totals.dev.judge == Decimal("0.75")
    assert report.format.totals.dev.judge == Decimal("0.25")
    assert report.quality.categories[-1].category == "Lain-lain"


def test_publish_regrade_keeps_old_rows(db_session, grading_data):
    session, attempt, user, old_rubric, old_run = grading_data

    old_rubric.is_active = False
    db_session.flush()

    new_rubric = DbRubricVersion(
        version_no=2,
        effective_date=date(2026, 11, 6),
        is_active=True,
        locked_at=NOW,
        judge_prompt_quality="Synthetic quality v2",
        judge_prompt_format="Synthetic format v2",
        created_by=user.id,
    )
    db_session.add(new_rubric)
    db_session.flush()

    new_run = GradingRun(
        session_id=session.id,
        rubric_version_id=new_rubric.id,
        kind=GradingRunKind.REGRADE,
        status=GradingRunStatus.FINISHED,
        finished_at=NOW,
        is_latest=False,
        triggered_by=user.id,
    )
    db_session.add(new_run)
    db_session.flush()

    new_result = make_result(db_session, new_run.id, attempt.id, "1.00")
    make_judge(db_session, new_result.id, JudgeKind.QUALITY, 4)
    make_judge(db_session, new_result.id, JudgeKind.FORMAT, 3)

    publish_latest_run(db_session, session.id, new_run.id)

    history = read_run_history(db_session, session.id)

    assert len(history) == 2
    assert [row.rubric_version_no for row in history] == [1, 2]
    assert [row.is_latest for row in history] == [False, True]
    assert old_run.rubric_version_id == old_rubric.id

    assert db_session.query(GradingResult).count() == 2
    assert active_rubric_version_no(db_session) == 2

    current = read_latest_grades(db_session, session.id)

    assert current.deterministic[attempt.id] == Decimal("1.00")
    assert current.quality[attempt.id] == Decimal("1.00")
    assert current.format[attempt.id] == Decimal("0.75")


def test_incomplete_regrade_cannot_be_published(db_session, grading_data):
    session, _, user, rubric, old_run = grading_data

    new_run = GradingRun(
        session_id=session.id,
        rubric_version_id=rubric.id,
        kind=GradingRunKind.REGRADE,
        status=GradingRunStatus.FINISHED,
        finished_at=NOW,
        triggered_by=user.id,
    )
    db_session.add(new_run)
    db_session.flush()

    with pytest.raises(ValueError, match="incomplete"):
        publish_latest_run(db_session, session.id, new_run.id)

    assert old_run.is_latest is True
    assert new_run.is_latest is False
