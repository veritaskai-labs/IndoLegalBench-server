import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.grading.models import (
    GradingResult,
    GradingResultReason,
    GradingRun,
    GradingRunKind,
    GradingRunStatus,
    JudgeKind,
    JudgeStatus,
    LlmCriterionResult,
    LlmJudgeResult,
)
from app.modules.grading.regrade_sql import (
    JudgeCriterion,
    JudgeOutcome,
    _running_result,
    append_sql_judge_failure,
    append_sql_judgement,
    finish_sql_regrade,
    mark_sql_regrade_failed,
    start_sql_regrade,
)


def _outcome(**changes):
    output = JudgeOutcome(
        attempt_id=uuid.uuid4(),
        kind=JudgeKind.QUALITY,
        judge_run_no=1,
        prompt="Test grading prompt",
        criteria=(
            JudgeCriterion("a", "Criterion A", True),
            JudgeCriterion("b", "Criterion B", False),
        ),
    )
    return replace(output, **changes)


def _rows(items):
    result = MagicMock()
    result.all.return_value = items
    return result


def test_start_regrade_copies_deterministic_results():
    db = MagicMock()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    old_rubric = uuid.uuid4()
    new_rubric = uuid.uuid4()

    old = SimpleNamespace(
        id=uuid.uuid4(),
        attempt_id=attempt_id,
        is_technical_disruption=False,
        det_score=Decimal("0.75"),
        det_regulation_matches=[],
        det_article_matches=[],
        det_ungradable_refs=[],
        det_all_chained_cited=True,
        det_reference_total=1,
        det_reference_correct=1,
        det_extracted_total=1,
        det_extracted_correct=1,
        det_formula="recall",
    )

    db.scalar.side_effect = [
        SimpleNamespace(id=session_id),
        SimpleNamespace(
            id=new_rubric,
            locked_at=datetime.now(UTC),
        ),
        None,
        SimpleNamespace(
            id=uuid.uuid4(),
            status=GradingRunStatus.FINISHED,
            rubric_version_id=old_rubric,
        ),
    ]

    db.scalars.side_effect = [
        _rows([old]),
        iter([attempt_id]),
        _rows([SimpleNamespace(reason_code="reference_missing")]),
    ]

    def assign_id(entity):
        if isinstance(entity, (GradingRun, GradingResult)):
            entity.id = uuid.uuid4()

    db.add.side_effect = assign_id

    run_id = start_sql_regrade(db, session_id, user_id)

    saved = [call.args[0] for call in db.add.call_args_list]

    run = next(item for item in saved if isinstance(item, GradingRun))
    result = next(item for item in saved if isinstance(item, GradingResult))
    reason = next(item for item in saved if isinstance(item, GradingResultReason))

    assert run_id == run.id
    assert run.kind == GradingRunKind.REGRADE
    assert run.status == GradingRunStatus.RUNNING
    assert run.is_latest is False
    assert run.rubric_version_id == new_rubric

    assert result.grading_run_id == run_id
    assert result.attempt_id == attempt_id
    assert result.det_score == Decimal("0.75")
    assert result.det_formula == "recall"
    assert reason.grading_result_id == result.id
    assert reason.reason_code == "reference_missing"


def test_same_rubric_skips_regrade():
    db = MagicMock()
    rubric_id = uuid.uuid4()

    db.scalar.side_effect = [
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(
            id=rubric_id,
            locked_at=datetime.now(UTC),
        ),
        None,
        SimpleNamespace(
            status=GradingRunStatus.FINISHED,
            rubric_version_id=rubric_id,
        ),
    ]

    assert start_sql_regrade(db, uuid.uuid4(), uuid.uuid4()) is None

    db.add.assert_not_called()


def test_unlocked_rubric_rejected():
    db = MagicMock()
    db.scalar.side_effect = [
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(id=uuid.uuid4(), locked_at=None),
    ]

    with pytest.raises(ValueError, match="active and locked"):
        start_sql_regrade(db, uuid.uuid4(), uuid.uuid4())

    db.add.assert_not_called()


def test_successful_judgement_saves_score_and_criteria():
    db = MagicMock()
    run_id = uuid.uuid4()
    output = _outcome()

    with patch(
        "app.modules.grading.regrade_sql._running_result",
        return_value=SimpleNamespace(id=uuid.uuid4()),
    ):
        append_sql_judgement(db, run_id, output)

    saved = [call.args[0] for call in db.add.call_args_list]

    judge = next(item for item in saved if isinstance(item, LlmJudgeResult))
    criteria = [item for item in saved if isinstance(item, LlmCriterionResult)]

    assert judge.status == JudgeStatus.SUCCEEDED
    assert judge.kind == JudgeKind.QUALITY
    assert judge.criteria_met == 1
    assert judge.criteria_total == 2
    assert judge.criteria_score == Decimal("0.50")

    assert len(criteria) == 2
    assert criteria[0].is_met is True
    assert criteria[0].reason_code is None
    assert criteria[1].is_met is False
    assert criteria[1].reason_code == "criterion_unmet"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"prompt": " "}, "prompt and criteria"),
        ({"criteria": ()}, "prompt and criteria"),
        ({"judge_run_no": 0}, "judge_run_no"),
        ({"judge_run_no": 6}, "judge_run_no"),
        (
            {
                "criteria": (
                    JudgeCriterion("a", "A", True),
                    JudgeCriterion("a", "A again", False),
                )
            },
            "duplicate judge criterion",
        ),
    ],
)
def test_invalid_judgement_rejected(changes, message):
    db = MagicMock()

    with patch(
        "app.modules.grading.regrade_sql._running_result",
        return_value=SimpleNamespace(id=uuid.uuid4()),
    ):
        with pytest.raises(ValueError, match=message):
            append_sql_judgement(
                db,
                uuid.uuid4(),
                _outcome(**changes),
            )

    db.add.assert_not_called()


def test_failed_judge_records_failure():
    db = MagicMock()

    with patch(
        "app.modules.grading.regrade_sql._running_result",
        return_value=SimpleNamespace(id=uuid.uuid4()),
    ):
        append_sql_judge_failure(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            JudgeKind.FORMAT,
            1,
            "Test prompt",
        )

    saved = db.add.call_args.args[0]

    assert isinstance(saved, LlmJudgeResult)
    assert saved.kind == JudgeKind.FORMAT
    assert saved.status == JudgeStatus.FAILED
    assert saved.criteria_score is None
    assert saved.failure_kind == "provider_error"


def test_running_result_returns_correct_attempt():
    db = MagicMock()
    expected = SimpleNamespace(
        id=uuid.uuid4(),
        is_technical_disruption=False,
    )

    db.get.return_value = SimpleNamespace(status=GradingRunStatus.RUNNING)
    db.scalar.return_value = expected

    actual = _running_result(db, uuid.uuid4(), uuid.uuid4())

    assert actual is expected


def test_running_result_rejects_finished_run():
    db = MagicMock()
    db.get.return_value = SimpleNamespace(status=GradingRunStatus.FINISHED)

    with pytest.raises(ValueError, match="not running"):
        _running_result(db, uuid.uuid4(), uuid.uuid4())


def test_running_result_rejects_disrupted_attempt():
    db = MagicMock()
    db.get.return_value = SimpleNamespace(status=GradingRunStatus.RUNNING)
    db.scalar.return_value = SimpleNamespace(is_technical_disruption=True)

    with pytest.raises(ValueError, match="not judgeable"):
        _running_result(db, uuid.uuid4(), uuid.uuid4())


def test_finish_regrade_publishes_complete_run():
    db = MagicMock()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    run = SimpleNamespace(
        kind=GradingRunKind.REGRADE,
        status=GradingRunStatus.RUNNING,
        rubric_version_id=uuid.uuid4(),
        finished_at=None,
    )

    db.scalar.side_effect = [
        SimpleNamespace(id=session_id),
        run,
    ]
    db.get.return_value = SimpleNamespace(is_active=True)

    with (
        patch("app.modules.grading.regrade_sql.assert_run_complete") as check,
        patch("app.modules.grading.regrade_sql.publish_latest_run") as publish,
    ):
        finish_sql_regrade(db, session_id, run_id)

    assert run.status == GradingRunStatus.FINISHED
    assert run.finished_at is not None
    check.assert_called_once_with(db, session_id, run_id)
    publish.assert_called_once_with(db, session_id, run_id)


def test_incomplete_regrade_is_not_published():
    db = MagicMock()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    run = SimpleNamespace(
        kind=GradingRunKind.REGRADE,
        status=GradingRunStatus.RUNNING,
        rubric_version_id=uuid.uuid4(),
        finished_at=None,
    )

    db.scalar.side_effect = [
        SimpleNamespace(id=session_id),
        run,
    ]
    db.get.return_value = SimpleNamespace(is_active=True)

    with (
        patch(
            "app.modules.grading.regrade_sql.assert_run_complete",
            side_effect=ValueError("incomplete grading"),
        ),
        patch("app.modules.grading.regrade_sql.publish_latest_run") as publish,
        pytest.raises(ValueError, match="incomplete"),
    ):
        finish_sql_regrade(db, session_id, run_id)
        assert run.status == GradingRunStatus.RUNNING
        publish.assert_not_called()


def test_failed_regrade_preserves_previous_latest():
    db = MagicMock()

    run = SimpleNamespace(
        kind=GradingRunKind.REGRADE,
        status=GradingRunStatus.RUNNING,
        is_latest=False,
        finished_at=None,
    )

    db.scalar.return_value = run

    mark_sql_regrade_failed(db, uuid.uuid4(), uuid.uuid4())

    assert run.status == GradingRunStatus.FAILED
    assert run.is_latest is False
    assert run.finished_at is not None
    db.flush.assert_called_once()


@pytest.mark.parametrize(
    "status",
    [
        GradingRunStatus.FINISHED,
        GradingRunStatus.RUNNING,
    ],
)
def test_cannot_fail_published_or_finished_run(status):
    db = MagicMock()

    run = SimpleNamespace(
        kind=GradingRunKind.REGRADE,
        status=status,
        is_latest=status == GradingRunStatus.RUNNING,
    )

    db.scalar.return_value = run

    with pytest.raises(ValueError, match="already completed"):
        mark_sql_regrade_failed(db, uuid.uuid4(), uuid.uuid4())
